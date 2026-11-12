#!/usr/bin/env python3
"""Opt-in shell shim installer and uncaptured-CLI process probe (#2870, epic #2856 slice T4)."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet_client import FleetArgumentError, FleetClient  # noqa: E402
from fleet_rec import CLI_AGENT_MAP, EVENTS_PATH, SUPPORTED_CLIS  # noqa: E402
from fleet_validators import PATTERNS, _check, _match, short_host  # noqa: E402

SHIM_BEGIN = "# >>> runner-dashboard fleet-rec shims >>>"
SHIM_END = "# <<< runner-dashboard fleet-rec shims <<<"
SUPPORTED_SHELLS = frozenset({"bash", "zsh", "powershell"})
SESSIONS_PATH = "/api/v1/activity/sessions"
_WIN_SUFFIX_RE = re.compile(r"(?i)\.(?:exe|cmd|ps1|bat)$")
_SHIM_BLOCK_RE = re.compile(
    rf"(?:\r?\n)?{re.escape(SHIM_BEGIN)}[\s\S]*?{re.escape(SHIM_END)}(?:\r?\n)?",
    re.MULTILINE,
)


def _normalize_proc_name(raw_name: str) -> str:
    leaf = re.split(r"[/\\]", str(raw_name or "").strip())[-1]
    return _WIN_SUFFIX_RE.sub("", leaf).lower()


def render_shell_shims(
    shell: str = "bash",
    *,
    python_exe: str | None = None,
    fleet_rec_path: str | Path | None = None,
) -> str:
    """Render an idempotent, reversible shell shim block for ``bash``, ``zsh``, or ``powershell``."""
    norm_shell = str(shell or "").strip().lower()
    _check(norm_shell in SUPPORTED_SHELLS, f"unsupported shell {shell!r}; expected bash, zsh, or powershell")
    script = str(Path(fleet_rec_path) if fleet_rec_path else Path(__file__).resolve().with_name("fleet_rec.py"))
    lines = [SHIM_BEGIN]
    if norm_shell == "powershell":
        py = python_exe or "python"
        for cli in SUPPORTED_CLIS:
            lines.append(
                f"function Invoke-FleetRec-{cli} {{ "
                f'if ($env:FLEET_REC_ENABLED -in @("1","true","yes","on")) {{ '
                f'& "{py}" "{script}" -- "{cli}" @args '
                f'}} else {{ & (Get-Command "{cli}" -CommandType Application | Select-Object -First 1) @args }} }}\n'
                f"Set-Alias -Name {cli} -Value Invoke-FleetRec-{cli} -Scope Global"
            )
    else:
        py = python_exe or "python3"
        for cli in SUPPORTED_CLIS:
            lines.append(
                f"{cli}() {{\n"
                f'  case "${{FLEET_REC_ENABLED:-}}" in\n'
                f'    1|true|TRUE|yes|YES|on|ON) "{py}" "{script}" -- "{cli}" "$@" ;;\n'
                f'    *) command "{cli}" "$@" ;;\n'
                f"  esac\n"
                f"}}"
            )
    lines.append(SHIM_END)
    return "\n".join(lines) + "\n"


def install_shell_shims(
    rc_path: str | Path,
    *,
    shell: str = "bash",
    python_exe: str | None = None,
    fleet_rec_path: str | Path | None = None,
) -> bool:
    """Install or update the ``fleet-rec`` shim block in ``rc_path`` idempotently."""
    target = Path(rc_path).expanduser()
    block = render_shell_shims(shell=shell, python_exe=python_exe, fleet_rec_path=fleet_rec_path)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if SHIM_BEGIN in existing and SHIM_END in existing:
        updated = _SHIM_BLOCK_RE.sub(
            ("\n" if existing and not existing.startswith(SHIM_BEGIN) else "") + block, existing
        )
    else:
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        updated = f"{existing}{sep}{block}"
    if updated == existing:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(updated, encoding="utf-8")
    return True


def uninstall_shell_shims(rc_path: str | Path) -> bool:
    """Remove the ``fleet-rec`` shim block from ``rc_path`` reversibly and idempotently."""
    target = Path(rc_path).expanduser()
    if not target.exists():
        return False
    existing = target.read_text(encoding="utf-8")
    if SHIM_BEGIN not in existing or SHIM_END not in existing:
        return False
    updated = _SHIM_BLOCK_RE.sub("\n", existing)
    if existing.endswith("\n") and not updated.endswith("\n"):
        updated += "\n"
    target.write_text(updated, encoding="utf-8")
    return True


def scan_local_cli_processes(proc_root: str | Path = "/proc") -> list[dict[str, Any]]:
    """Scan ``/proc`` for running fleet CLI processes, reading ONLY executable name and start time."""
    root = Path(proc_root)
    if not root.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        comm_file = entry / "comm"
        try:
            comm = _normalize_proc_name(comm_file.read_text(encoding="utf-8", errors="ignore").strip())
            if comm not in SUPPORTED_CLIS:
                continue
            stat_mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc).isoformat()  # noqa: UP017
            found.append({"pid": int(entry.name), "process_name": comm, "started_at": stat_mtime})
        except OSError:
            continue
    return found


def probe_uncaptured_processes(**opts: Any) -> dict[str, Any]:
    """Report running CLI processes without an active registered session as ``uncaptured``."""
    raw_node = (opts.get("node") or os.environ.get("FLEET_NODE") or short_host()).strip()[:128]
    node = _match(PATTERNS.ident, raw_node, "node")
    client = FleetClient(
        opts.get("url"),
        token=opts.get("token"),
        agent="antigravity",
        session=f"antigravity-probe-{node}",
    )
    resp = client.request("GET", SESSIONS_PATH, params={"node": node}, retry_idempotent=True)
    items = resp.get("items", []) if isinstance(resp, dict) else []
    active_ids = {
        str(it.get("session_id"))
        for it in items
        if isinstance(it, dict) and it.get("status") in {"live", "idle"} and it.get("session_id")
    }
    active_agents = {
        str(it.get("session_id", "")).split("-", 1)[0]
        for it in items
        if isinstance(it, dict) and it.get("status") in {"live", "idle"} and it.get("session_id")
    }

    scanner: Callable[[], Iterable[dict[str, Any]]] = opts.get("process_scanner") or scan_local_cli_processes
    envelopes: list[dict[str, Any]] = []
    reported_sessions: list[str] = []
    now_iso = datetime.now(timezone.utc).isoformat()  # noqa: UP017

    for raw in scanner():
        if not isinstance(raw, dict):
            continue
        cli_name = _normalize_proc_name(str(raw.get("process_name") or ""))
        if cli_name not in SUPPORTED_CLIS:
            continue
        pid = int(raw.get("pid") or 0)
        _check(pid > 0, "process pid must be positive")
        bound_sid = str(raw.get("session_id") or "").strip()
        agent = CLI_AGENT_MAP[cli_name]
        if (bound_sid and bound_sid in active_ids) or (not bound_sid and agent in active_agents):
            continue
        started_at = str(raw.get("started_at") or now_iso).strip()
        sid = _match(PATTERNS.session, f"{agent}-uncaptured-{node}-{pid}", "session")
        # DbC postcondition: payload contains ONLY process_name and started_at (never cmdline/args).
        payload = {"process_name": cli_name, "started_at": started_at}
        _check(set(payload.keys()) == {"process_name", "started_at"}, "probe payload must not include arguments")
        envelopes.append(
            {
                "schema_version": 1,
                "event_id": f"recorder:{sid}:1",
                "source": "recorder",
                "principal": f"agent-{agent}",
                "node": node,
                "session_id": sid,
                "seq": 1,
                "ts": now_iso,
                "repo": None,
                "issue": None,
                "branch": None,
                "pr": None,
                "kind": "uncaptured_process",
                "redaction_version": 1,
                "payload": payload,
            }
        )
        reported_sessions.append(sid)

    if envelopes:
        client.request("POST", EVENTS_PATH, body=envelopes, retry_idempotent=False)
    return {"ok": True, "node": node, "uncaptured_count": len(envelopes), "reported_sessions": reported_sessions}


def emit_recorder_heartbeat(**opts: Any) -> dict[str, Any]:
    """Emit a recorder/adapter health heartbeat envelope."""
    raw_node = (opts.get("node") or os.environ.get("FLEET_NODE") or short_host()).strip()[:128]
    node = _match(PATTERNS.ident, raw_node, "node")
    adapter = _match(PATTERNS.ident, str(opts.get("adapter") or "pty"), "adapter")
    uncaptured_count = int(opts.get("uncaptured_count") or 0)
    _check(uncaptured_count >= 0, "uncaptured_count must be >= 0")
    seq = int(opts.get("seq") or 1)
    sid = _match(PATTERNS.session, f"antigravity-probe-{node}", "session")
    now_iso = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    client = FleetClient(opts.get("url"), token=opts.get("token"), agent="antigravity", session=sid)
    ev = {
        "schema_version": 1,
        "event_id": f"recorder:{sid}:{seq}",
        "source": "recorder",
        "principal": "agent-antigravity",
        "node": node,
        "session_id": sid,
        "seq": seq,
        "ts": now_iso,
        "repo": None,
        "issue": None,
        "branch": None,
        "pr": None,
        "kind": "recorder_heartbeat",
        "redaction_version": 1,
        "payload": {
            "adapter": adapter,
            "healthy": bool(opts.get("healthy", True)),
            "uncaptured_count": uncaptured_count,
        },
    }
    client.request("POST", EVENTS_PATH, body=[ev], retry_idempotent=False)
    return {"ok": True, "node": node, "session_id": sid, "seq": seq}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fleet-probe", description="Shell shims and uncaptured-CLI probe (#2870).")
    sub = p.add_subparsers(dest="subcmd", required=True)
    inst = sub.add_parser("install-shims")
    inst.add_argument("--rc-file", required=True)
    inst.add_argument("--shell", default="bash")
    uninst = sub.add_parser("uninstall-shims")
    uninst.add_argument("--rc-file", required=True)
    probe = sub.add_parser("probe")
    probe.add_argument("--url")
    probe.add_argument("--node")
    ns = p.parse_args(argv)
    try:
        if ns.subcmd == "install-shims":
            install_shell_shims(ns.rc_file, shell=ns.shell)
        elif ns.subcmd == "uninstall-shims":
            uninstall_shell_shims(ns.rc_file)
        else:
            res = probe_uncaptured_processes(url=ns.url, node=ns.node)
            emit_recorder_heartbeat(url=ns.url, node=ns.node, uncaptured_count=res["uncaptured_count"])
        return 0
    except FleetArgumentError as exc:
        sys.stderr.write(f"fleet-probe: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
