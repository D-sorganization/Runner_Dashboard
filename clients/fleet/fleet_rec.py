#!/usr/bin/env python3
"""Opt-in Linux/WSL PTY and Windows ConPTY/PowerShell session recorder for fleet CLIs (#2868, #2869)."""

from __future__ import annotations

import argparse
import contextlib
import errno
import json
import os
import re
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
    import pty
    import termios
    import tty
except ImportError:  # pragma: no cover - Windows runtime lacks POSIX pty/termios
    fcntl = pty = termios = tty = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet_client import FleetAPIError, FleetArgumentError, FleetClient  # noqa: E402
from fleet_validators import PATTERNS, _check, _match, _opt, _positive_int, default_session, short_host  # noqa: E402

SUPPORTED_CLIS: tuple[str, ...] = ("claude", "codex", "agy", "cursor-agent")
CLI_AGENT_MAP: dict[str, str] = {"claude": "claude", "codex": "codex", "agy": "antigravity", "cursor-agent": "cursor"}
_WIN_SUFFIX_RE = re.compile(r"(?i)\.(?:exe|cmd|ps1|bat)$")
_POSIX_PLATFORMS = frozenset({"posix", "linux", "wsl"})
_WINDOWS_PLATFORMS = frozenset({"windows", "win32", "nt", "conpty", "powershell"})
EVENTS_PATH = "/api/v1/activity/events"
DEFAULT_MAX_CHUNK_BYTES = 32_768
DEFAULT_MAX_SPOOL_CHUNKS = 200
DEFAULT_MAX_SPOOL_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_BASE_BACKOFF = 0.1
_RETRYABLE_STATUSES = frozenset({0, 429, 502, 503, 504})
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})

_ANSI_RE = re.compile(r"(\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_])")
_AWS_SECRET_RE = re.compile(
    r"(?i)\b(?:aws_secret_access_key|aws_secret_key|secret_key)\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?"
)
_OCT = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_IP_PAT = (
    rf"\b(?:10\.(?:{_OCT}\.){{2}}{_OCT}|172\.(?:1[6-9]|2[0-9]|3[0-1])\.{_OCT}\.{_OCT}|"
    rf"192\.168\.{_OCT}\.{_OCT}|"
    rf"100\.(?:6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7])\.{_OCT}\.{_OCT})\b"  # pragma: allowlist private-topology
)
_SUB_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{35,254}|github_pat_[A-Za-z0-9_]{22,254})\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\b(?:AKIA|ASIA|AROA)[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\b(?:sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,})\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{20,}\b"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(_IP_PAT), "[REDACTED_IP]"),
)


def _redact_plain(text: str) -> str:
    if not text:
        return text
    s = _AWS_SECRET_RE.sub(lambda m: m.group(0).replace(m.group(1), "[REDACTED_AWS_SECRET_KEY]"), text)
    for pat, repl in _SUB_RULES:
        s = pat.sub(repl, s)
    return s


def redact_node_stream(text: str) -> str:
    """Redact secrets and private IPs on the node before spooling, preserving ANSI sequences."""
    if not text:
        return text
    parts = _ANSI_RE.split(text)
    redacted = "".join(part if idx % 2 else _redact_plain(part) for idx, part in enumerate(parts))
    plain = _ANSI_RE.sub("", redacted)
    return _redact_plain(plain) if _redact_plain(plain) != plain else redacted


def is_recorder_enabled(explicit: bool | None = None) -> bool:
    return (
        bool(explicit)
        if explicit is not None
        else os.environ.get("FLEET_REC_ENABLED", "").strip().lower() in _ENABLED_VALUES
    )


def normalize_cli_name(raw_arg0: str) -> str:
    """Normalize a POSIX or Windows/PowerShell CLI executable path to its canonical CLI name."""
    leaf = re.split(r"[/\\]", str(raw_arg0 or "").strip())[-1]
    return _WIN_SUFFIX_RE.sub("", leaf).lower()


def _resolve_platform(explicit: str | None = None) -> str:
    raw = str(explicit or os.environ.get("FLEET_REC_PLATFORM") or ("windows" if os.name == "nt" else "posix")).strip()
    low = raw.lower()
    if low in _POSIX_PLATFORMS:
        return "posix"
    if low in _WINDOWS_PLATFORMS:
        return "windows"
    raise FleetArgumentError(f"unsupported recorder platform {raw!r}; expected posix or windows")


def default_recorder_spool_path(explicit: str | Path | None = None, *, platform: str | None = None) -> Path:
    if raw := str(explicit or os.environ.get("FLEET_REC_SPOOL") or "").strip():
        return Path(raw).expanduser()
    plat = _resolve_platform(platform)
    cfg = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR", "").strip()
    if plat == "windows":
        win_base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Local")
        root = Path(cfg).expanduser() if cfg else Path(win_base).expanduser() / "runner-dashboard"
        return root / "fleet-rec-spool-windows.json"
    root = (
        Path(cfg).expanduser()
        if cfg
        else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser() / "runner-dashboard"
    )
    return root / "fleet-rec-spool.json"


def _load_spool(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "events": [], "chunks": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FleetArgumentError(f"corrupt recorder spool file {path}: {exc}") from exc
    _check(isinstance(data, dict) and data.get("schema_version") == 1, "unsupported recorder spool schema_version")
    events, chunks = data.get("events"), data.get("chunks")
    _check(isinstance(events, list) and isinstance(chunks, list), "malformed recorder spool structure")
    return {"schema_version": 1, "events": list(events), "chunks": list(chunks)}


def _save_spool(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    with contextlib.suppress(OSError):
        tmp.chmod(0o600)
    os.replace(tmp, path)


def _envelope(sid: str, seq: int, kind: str, payload: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    ts = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    base = {"schema_version": 1, "event_id": f"recorder:{sid}:{seq}", "source": "recorder", "session_id": sid}
    meta = {"seq": seq, "ts": ts, "kind": kind, "redaction_version": 1, "payload": payload}
    return {**base, **ctx, **meta}


def _bound_chunks(state: dict[str, Any], max_chunks: int, max_bytes: int, sid: str, ctx: dict[str, Any]) -> None:
    chunks: list[dict[str, Any]] = state["chunks"]
    dropped_chunks = dropped_bytes = last_seq = 0
    while len(chunks) > max_chunks or (
        len(chunks) > 1 and sum(len(str(c.get("data", "")).encode("utf-8")) for c in chunks) > max_bytes
    ):
        evicted = chunks.pop(0)
        dropped_chunks += 1
        dropped_bytes += len(str(evicted.get("data", "")).encode("utf-8"))
        last_seq = int(evicted.get("seq", last_seq))
        state["events"] = [
            e
            for e in state["events"]
            if not (e.get("session_id") == sid and e.get("kind") == "chunk" and e.get("seq") == last_seq)
        ]
    if dropped_chunks > 0:
        for ev in state["events"]:
            if ev.get("session_id") == sid and ev.get("kind") == "OverflowGap":
                p = ev["payload"]
                p["dropped_chunks"] = int(p.get("dropped_chunks", 0)) + dropped_chunks
                p["dropped_bytes"] = int(p.get("dropped_bytes", 0)) + dropped_bytes
                p["chunk_seq"] = last_seq
                return
        gap = {
            "reason": "local_spool_overflow",
            "dropped_chunks": dropped_chunks,
            "dropped_bytes": dropped_bytes,
            "chunk_seq": last_seq,
        }
        state["events"].append(_envelope(sid, last_seq, "OverflowGap", gap, ctx))


def _post_retry(
    client: FleetClient, path: str, body: Any, max_attempts: int, base_backoff: float, sleep_fn: Callable[[float], None]
) -> bool:
    for attempt in range(max_attempts):
        try:
            client.request("POST", path, body=body, timeout=min(client.timeout, 5.0), retry_idempotent=False)
            return True
        except FleetAPIError as exc:
            if exc.status not in _RETRYABLE_STATUSES:
                raise
            if attempt < max_attempts - 1 and base_backoff > 0:
                sleep_fn(base_backoff * (2**attempt))
    return False


def flush_recorder_spool(client: FleetClient, spool_file: str | Path | None = None, **kw: Any) -> dict[str, Any]:
    """Replay queued recorder events and chunks from the local spool in order."""
    max_att = int(kw.get("max_attempts", DEFAULT_MAX_ATTEMPTS))
    backoff = float(kw.get("base_backoff", DEFAULT_BASE_BACKOFF))
    sleep_fn = kw.get("sleep_fn", time.sleep)
    path = default_recorder_spool_path(spool_file)
    state = _load_spool(path)
    events, chunks = state["events"], state["chunks"]
    del_ev = del_ch = 0
    while events:
        batch = events[:20]
        if not _post_retry(client, EVENTS_PATH, batch, max_att, backoff, sleep_fn):
            break
        del_ev += len(batch)
        state["events"] = events = events[len(batch) :]
        _save_spool(path, state)
    while not events and chunks:
        it = chunks[0]
        body = {"seq": it["seq"], "node": it["node"], "source": "recorder", "principal": it["principal"]}
        body.update(data=it["data"], redaction_version=1)
        if not _post_retry(
            client, f"/api/v1/activity/sessions/{it['session_id']}/chunks", body, max_att, backoff, sleep_fn
        ):
            break
        del_ch += 1
        chunks.pop(0)
        state["chunks"] = chunks
        _save_spool(path, state)
    res = {"ok": True, "degraded": bool(events or chunks), "delivered_events": del_ev, "delivered_chunks": del_ch}
    return {**res, "pending_events": len(events), "pending_chunks": len(chunks)}


def _sync_winsize(src_fd: int, dst_fd: int) -> None:
    with contextlib.suppress(OSError):
        fcntl.ioctl(dst_fd, termios.TIOCSWINSZ, fcntl.ioctl(src_fd, termios.TIOCGWINSZ, b"\x00" * 8))


def run_pty_process(argv: Sequence[str], *, on_output: Callable[[bytes], None], **kw: Any) -> int:
    """Spawn ``argv`` in a Linux/WSL PTY, preserving interactivity and exit code."""
    _check(bool(argv), "argv must not be empty")
    master_fd, slave_fd = pty.openpty()
    stdin_fd: int | None = None
    old_tty: list[Any] | None = None
    old_winch: Any = None
    try:
        with contextlib.suppress(AttributeError, OSError, ValueError):
            if os.isatty(cand := sys.stdin.fileno()):
                stdin_fd, old_tty = cand, termios.tcgetattr(cand)
                _sync_winsize(stdin_fd, slave_fd)
                tty.setraw(stdin_fd)
        p_kw: dict[str, Any] = {"stdin": slave_fd, "stdout": slave_fd, "stderr": slave_fd, "start_new_session": True}
        proc = subprocess.Popen(list(argv), close_fds=True, **p_kw)
        os.close(slave_fd)
        slave_fd = -1
        if stdin_fd is not None:
            old_winch = signal.signal(signal.SIGWINCH, lambda _s, _f: _sync_winsize(stdin_fd, master_fd))
        if initial_input := kw.get("initial_input"):
            os.write(master_fd, initial_input)
        watch_fds = [master_fd] + ([stdin_fd] if stdin_fd is not None else [])
        while watch_fds:
            try:
                ready, _, _ = select.select(watch_fds, [], [], 0.1)
            except InterruptedError:
                continue
            if master_fd in ready:
                try:
                    data = os.read(master_fd, DEFAULT_MAX_CHUNK_BYTES)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                if kw.get("passthrough_stdout"):
                    with contextlib.suppress(OSError):
                        os.write(sys.stdout.fileno(), data)
                on_output(data)
            if stdin_fd is not None and stdin_fd in ready:
                user_in = b""
                with contextlib.suppress(OSError):
                    user_in = os.read(stdin_fd, 4096)
                if user_in:
                    os.write(master_fd, user_in)
                else:
                    watch_fds = [fd for fd in watch_fds if fd != stdin_fd]
        return rc if (rc := proc.wait()) >= 0 else 128 + (-rc)
    finally:
        if old_winch is not None:
            with contextlib.suppress(OSError, ValueError):
                signal.signal(signal.SIGWINCH, old_winch)
        if stdin_fd is not None and old_tty is not None:
            with contextlib.suppress(termios.error, OSError):
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tty)
        for fd in (slave_fd, master_fd):
            with contextlib.suppress(OSError):
                if fd >= 0:
                    os.close(fd)


def run_conpty_process(argv: Sequence[str], *, on_output: Callable[[bytes], None], **kw: Any) -> int:
    """Spawn ``argv`` in a Windows ConPTY / PowerShell stream harness and stream chunks."""
    _check(bool(argv) and bool(str(argv[0]).strip()), "argv must not be empty")
    _check(callable(on_output), "on_output must be callable")
    cmd = list(kw.get("conpty_argv") or argv)
    initial_input: bytes | None = kw.get("initial_input")
    if factory := kw.get("conpty_factory"):
        proc_pty = factory(cmd, cols=int(kw.get("cols", 120)), rows=int(kw.get("rows", 32)))
        if initial_input and hasattr(proc_pty, "write"):
            proc_pty.write(initial_input)
        while True:
            chunk = proc_pty.read(DEFAULT_MAX_CHUNK_BYTES)
            if not chunk:
                break
            raw = chunk.encode("utf-8", errors="replace") if isinstance(chunk, str) else bytes(chunk)
            if kw.get("passthrough_stdout"):
                with contextlib.suppress(OSError):
                    os.write(sys.stdout.fileno(), raw)
            on_output(raw)
        return int(proc_pty.wait())

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.stdin is not None:
        if initial_input:
            with contextlib.suppress(OSError):
                proc.stdin.write(initial_input)
                proc.stdin.flush()
        with contextlib.suppress(OSError):
            proc.stdin.close()
    if proc.stdout is not None:
        read_fn = getattr(proc.stdout, "read1", proc.stdout.read)
        while data := read_fn(DEFAULT_MAX_CHUNK_BYTES):
            if kw.get("passthrough_stdout"):
                with contextlib.suppress(OSError):
                    os.write(sys.stdout.fileno(), data)
            on_output(data)
    return int(proc.wait())


def render_powershell_profile_snippet(python_exe: str = "python", script_path: str | Path | None = None) -> str:
    """Render PowerShell ``$PROFILE`` wrappers routing fleet CLIs through ``fleet-rec`` on Windows."""
    script = str(Path(script_path) if script_path else Path(__file__).resolve())
    lines = ["# Runner Dashboard fleet-rec PowerShell wrappers (#2869)"]
    for cli in SUPPORTED_CLIS:
        lines.append(
            f"function Invoke-FleetRec-{cli} {{ "
            f'if ($env:FLEET_REC_ENABLED -in @("1","true","yes","on")) {{ '
            f'& "{python_exe}" "{script}" --platform windows -- "{cli}" @args '
            f'}} else {{ & (Get-Command "{cli}" -CommandType Application | Select-Object -First 1) @args }} }}\n'
            f"Set-Alias -Name {cli} -Value Invoke-FleetRec-{cli} -Scope Global"
        )
    return "\n".join(lines) + "\n"


def run_recorded_cli(argv: Sequence[str], **opts: Any) -> int:
    """Run ``argv`` wrapped in ``fleet-rec`` when opt-in node recording is enabled."""
    _check(bool(argv) and bool(str(argv[0]).strip()), "command argv must not be empty")
    cli_name = normalize_cli_name(str(argv[0]))
    _check(cli_name in SUPPORTED_CLIS, f"unsupported CLI {cli_name!r}; fleet-rec wraps {', '.join(SUPPORTED_CLIS)}")
    plat = _resolve_platform(opts.get("platform"))
    max_chunks = int(opts.get("max_spool_chunks", DEFAULT_MAX_SPOOL_CHUNKS))
    max_bytes = int(opts.get("max_spool_bytes", DEFAULT_MAX_SPOOL_BYTES))
    _check(max_chunks >= 1 and max_bytes >= 1, "spool bounds must be >= 1")
    if not is_recorder_enabled(opts.get("enabled")):
        return int((opts.get("passthrough_runner") or subprocess.call)(list(argv)))

    spool_path = default_recorder_spool_path(opts.get("spool_file"), platform=plat)
    state = _load_spool(spool_path)
    raw_ag = opts.get("agent") or os.environ.get("FLEET_AGENT") or CLI_AGENT_MAP[cli_name]
    agent = _match(PATTERNS.agent, raw_ag, "agent")
    raw_sid = opts.get("session") or os.environ.get("FLEET_SESSION") or default_session(agent)
    sid = _match(PATTERNS.session, raw_sid, "session")
    raw_node = (opts.get("node") or os.environ.get("FLEET_NODE") or short_host()).strip()[:128]
    node = _match(PATTERNS.ident, raw_node, "node")
    raw_princ = (opts.get("principal") or os.environ.get("FLEET_PRINCIPAL") or f"agent-{agent}").strip()
    principal = _match(PATTERNS.ident, raw_princ, "principal")
    ctx: dict[str, Any] = {"node": node, "principal": principal, "repo": _opt(PATTERNS.repo, opts.get("repo"), "repo")}
    ctx["issue"] = None if opts.get("issue") is None else _positive_int(opts["issue"], "issue")
    ctx["branch"] = _opt(PATTERNS.branch, opts.get("branch"), "branch")
    ctx["pr"] = None if opts.get("pr") is None else _positive_int(opts["pr"], "pr")
    client = FleetClient(opts.get("url"), token=opts.get("token"), agent=agent, session=sid)
    fkw = {k: opts[k] for k in ("max_attempts", "base_backoff", "sleep_fn") if k in opts}
    seq = 0
    state["events"].append(_envelope(sid, seq, "session_start", {"cli": cli_name}, ctx))
    _save_spool(spool_path, state)
    flush_recorder_spool(client, spool_path, **fkw)

    def _on_chunk(raw_bytes: bytes) -> None:
        nonlocal seq, state
        if not raw_bytes or not (redacted := redact_node_stream(raw_bytes.decode("utf-8", errors="replace"))):
            return
        seq += 1
        state = _load_spool(spool_path)
        state["events"].append(_envelope(sid, seq, "chunk", {"byte_len": len(redacted.encode("utf-8"))}, ctx))
        state["chunks"].append({"session_id": sid, "seq": seq, "node": node, "principal": principal, "data": redacted})
        _bound_chunks(state, max_chunks, max_bytes, sid, ctx)
        _save_spool(spool_path, state)
        flush_recorder_spool(client, spool_path, **fkw)

    exit_code = 1
    try:
        rkw = {k: opts[k] for k in ("conpty_factory", "conpty_argv", "initial_input") if k in opts}
        default_run = (
            (lambda cmd, cb: run_conpty_process(cmd, on_output=cb, passthrough_stdout=True, **rkw))
            if plat == "windows"
            else (lambda cmd, cb: run_pty_process(cmd, on_output=cb, passthrough_stdout=True))
        )
        runner = opts.get("pty_runner") or default_run
        exit_code = int(runner(list(argv), _on_chunk))
        return exit_code
    finally:
        seq += 1
        state = _load_spool(spool_path)
        state["events"].append(_envelope(sid, seq, "session_end", {"cli": cli_name, "exit_code": exit_code}, ctx))
        _save_spool(spool_path, state)
        flush_recorder_spool(client, spool_path, **fkw)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="fleet-rec", description="Opt-in PTY/ConPTY recorder for fleet CLIs (#2868/#2869)."
    )
    for flag in ("--url", "--session", "--agent", "--node", "--repo", "--branch", "--spool-file", "--platform"):
        p.add_argument(flag)
    p.add_argument("--enable", action="store_true", default=None)
    p.add_argument("--print-powershell-profile", action="store_true")
    p.add_argument("--issue", type=int)
    p.add_argument("--pr", type=int)
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    ns = p.parse_args(argv)
    if ns.print_powershell_profile:
        sys.stdout.write(render_powershell_profile_snippet())
        return 0
    cmd = list(ns.cmd[1:] if ns.cmd and ns.cmd[0] == "--" else ns.cmd)
    keys = ("url", "agent", "session", "node", "repo", "issue", "branch", "pr", "spool_file", "platform")
    kw = {k: getattr(ns, k) for k in keys}
    try:
        return run_recorded_cli(cmd, enabled=True if ns.enable else None, **kw)
    except FleetArgumentError as exc:
        sys.stderr.write(f"fleet-rec: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
