#!/usr/bin/env python3
"""Spawn backlog-push lanes detached from the panel-relay oneshot (#4098).

Reads a JSON plan array on stdin; prints a JSON object of role->pid on stdout.
Never prints tokens. Unsets GH_TOKEN/GITHUB_TOKEN so lanes use the hub fleet gh wrapper.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_SIGKILL = getattr(signal, "SIGKILL", 9)


def _lane_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    return env


def _is_active_process(pid: int) -> bool:
    """Return True if pid is positive and currently alive."""
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _get_process_cmdline(pid: int) -> list[str] | None:
    """Return command-line argument list for pid if available, else None."""
    proc_cmd = Path(f"/proc/{pid}/cmdline")
    if proc_cmd.is_file():
        try:
            raw = proc_cmd.read_bytes()
            return [p.decode("utf-8", errors="replace") for p in raw.split(b"\x00") if p]
        except OSError:
            return None
    try:
        import psutil  # type: ignore[import-untyped]

        return list(psutil.Process(pid).cmdline())
    except Exception:
        return None


_LANE_ENTRYPOINT = "backlog-push-lane.sh"


def _get_process_ppid(pid: int) -> int | None:
    """Return the parent pid for pid if available, else None."""
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        try:
            raw = proc_stat.read_text(encoding="utf-8", errors="replace")
            # Format: "pid (comm) state ppid ...". comm can itself contain
            # ")" or whitespace, so split on the last ")" rather than token 1.
            after_comm = raw.rsplit(")", 1)[-1].split()
            return int(after_comm[1])
        except (OSError, IndexError, ValueError):
            return None
    try:
        import psutil  # type: ignore[import-untyped]

        return psutil.Process(pid).ppid()
    except Exception:
        return None


_SHELL_INTERPRETER_NAMES = {"bash", "sh", "dash", "zsh"}


def _verify_process_identity(pid: int, session: str) -> bool:
    """Verify that an alive process is this session's lane worker (#4103, hardened #4150, #4151).

    The recorded pid is the worker launched by ``backlog-push-lane.sh`` (the
    ``claude`` child itself), whose own argv never carries the entrypoint name
    or session token. Identity instead comes from its parent process — the
    lane script invocation ``bash backlog-push-lane.sh SESSION ...`` (the
    script itself reads ``SESSION="${1:?session}"``) — which does.

    Checking for the entrypoint name and the session token independently
    anywhere in argv is not enough: ``tail backlog-push-lane.sh lane-a`` or
    ``echo lane-a backlog-push-lane.sh`` both contain both strings without
    being the lane invocation at all. Require the actual invocation shape:
    the entrypoint at argv[0] (direct exec) or argv[1] preceded by a shell
    interpreter at argv[0], with the session token as the very next
    positional argument — mirroring the script's own ``$1`` read.
    """
    if not _is_active_process(pid):
        return False
    ppid = _get_process_ppid(pid)
    if ppid is None:
        return False
    cmdline = _get_process_cmdline(ppid)
    if not cmdline:
        return False
    if Path(cmdline[0]).name == _LANE_ENTRYPOINT:
        entry_idx = 0
    elif (
        Path(cmdline[0]).name in _SHELL_INTERPRETER_NAMES
        and len(cmdline) > 1
        and Path(cmdline[1]).name == _LANE_ENTRYPOINT
    ):
        entry_idx = 1
    else:
        return False
    session_idx = entry_idx + 1
    return session_idx < len(cmdline) and cmdline[session_idx] == session


def _record_recovery_evidence(
    log_dir: Path,
    key: str,
    details: dict[str, Any],
    cleanup_failures: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Retain reconstructible recovery evidence in recovery_evidence.json (#4103)."""
    evidence_path = log_dir / "recovery_evidence.json"
    data: dict[str, Any] = {"records": {}, "updated_at": time.time()}
    if evidence_path.is_file():
        try:
            loaded = json.loads(evidence_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            pass
    data.setdefault("records", {})[key] = details
    if cleanup_failures:
        data.setdefault("cleanup_failures", {}).update(cleanup_failures)
    data["timestamp"] = data["updated_at"] = time.time()
    try:
        evidence_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def _write_lane_receipt(log_dir: Path, session: str, role: str, pid: int, status: str = "active") -> Path:
    """Durable per-lane ownership receipt written immediately upon launch (#4103)."""
    receipt_path = log_dir / f"{session}.receipt.json"
    data = {"session": session, "role": role, "pid": pid, "status": status, "updated_at": time.time()}
    try:
        receipt_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        _record_recovery_evidence(
            log_dir,
            f"receipt_failure_{session}",
            {"session": session, "role": role, "pid": pid, "status": status, "error": str(exc)},
        )
        raise
    return receipt_path


def _update_partial_manifest(log_dir: Path, launched: list[tuple[str, int]], pids: dict[str, int]) -> Path:
    """Update recoverable partial-active record as lanes launch (#4103)."""
    partial_path = log_dir / "partial_active.json"
    data = {"sessions": [s for s, _ in launched], "pids": pids, "updated_at": time.time()}
    partial_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return partial_path


def _rollback_launched(log_dir: Path, newly_spawned: list[tuple[str, int]]) -> None:
    """Stop only workers newly created in this spawn attempt (#4103)."""
    cleanup_failures: dict[str, dict[str, Any]] = {}
    for session, pid in reversed(newly_spawned):
        if pid <= 1:
            continue
        receipt_path = log_dir / f"{session}.receipt.json"
        exited = not _is_active_process(pid)
        if not exited:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as exc:
                if getattr(exc, "errno", None) == errno.ESRCH:
                    exited = True
            for _ in range(10):
                if exited or not _is_active_process(pid):
                    exited = True
                    break
                time.sleep(0.05)
            if not exited:
                try:
                    os.kill(pid, _SIGKILL)
                except OSError as exc:
                    if getattr(exc, "errno", None) == errno.ESRCH:
                        exited = True
                for _ in range(5):
                    if exited or not _is_active_process(pid):
                        exited = True
                        break
                    time.sleep(0.05)

        if exited:
            if receipt_path.is_file():
                try:
                    rec = json.loads(receipt_path.read_text(encoding="utf-8"))
                    rec.update({"status": "rolled_back", "rolled_back_at": time.time()})
                    receipt_path.write_text(json.dumps(rec, indent=2, sort_keys=True), encoding="utf-8")
                except Exception:
                    pass
            (log_dir / f"{session}.pid").unlink(missing_ok=True)
            (log_dir / f"{session}.exit").unlink(missing_ok=True)
        else:
            if receipt_path.is_file():
                try:
                    rec = json.loads(receipt_path.read_text(encoding="utf-8"))
                    rec.update({"status": "termination_pending", "pending_at": time.time()})
                    receipt_path.write_text(json.dumps(rec, indent=2, sort_keys=True), encoding="utf-8")
                except Exception:
                    pass
            cleanup_failures[session] = {
                "pid": pid,
                "status": "termination_pending",
                "error": "process alive after SIGTERM and SIGKILL attempts",
                "timestamp": time.time(),
            }

    if cleanup_failures:
        _record_recovery_evidence(log_dir, "rollback_failures", {"timestamp": time.time()}, cleanup_failures)


def _spawn_one(
    lane_sh: Path,
    session: str,
    log_path: Path,
    prompt_path: Path,
    pidfile: Path,
    env: dict[str, str],
) -> int:
    pidfile.unlink(missing_ok=True)
    if shutil.which("systemd-run") and env.get("XDG_RUNTIME_DIR"):
        unit = "barb-bpush-" + "".join(c if c.isalnum() else "-" for c in session)[:200]
        subprocess.Popen(
            [
                "systemd-run",
                "--user",
                "--scope",
                "--collect",
                f"--unit={unit}",
                "bash",
                str(lane_sh),
                session,
                str(log_path),
                str(prompt_path),
                str(pidfile),
            ],
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            ["setsid", "bash", str(lane_sh), session, str(log_path), str(prompt_path), str(pidfile)],
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    for _ in range(100):
        if pidfile.is_file() and pidfile.stat().st_size > 0:
            return int(pidfile.read_text(encoding="utf-8").strip())
        time.sleep(0.1)
    raise RuntimeError(f"backlog-push-spawn: no pid for session {session!r}")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: backlog-push-spawn.py LOG_DIR", file=sys.stderr)
        return 2
    log_dir = Path(args[0])
    log_dir.mkdir(parents=True, exist_ok=True)
    root = Path(os.environ.get("RUNNER_DASHBOARD_ROOT", "/opt/Runner_Dashboard"))
    lane_sh = root / "scripts/barb-worker/backlog-push-lane.sh"
    if not lane_sh.is_file():
        print(f"backlog-push-spawn: missing {lane_sh}", file=sys.stderr)
        return 2
    plan: list[dict[str, Any]] = json.load(sys.stdin)
    env = _lane_env()
    out: dict[str, int] = {}
    newly_spawned: list[tuple[str, int]] = []
    recovered: list[tuple[str, int]] = []
    lane_n = 0
    try:
        for entry in plan:
            role = str(entry["role"])
            session = str(entry["session"])
            prompt_path = log_dir / f"{session}.prompt"
            prompt_path.write_text(str(entry["prompt"]), encoding="utf-8")
            log_path = log_dir / f"{session}.log"
            log_path.touch()
            pidfile = log_dir / f"{session}.pid"

            existing_pid: int | None = None
            if pidfile.is_file() and pidfile.stat().st_size > 0:
                try:
                    candidate = int(pidfile.read_text(encoding="utf-8").strip())
                    if _verify_process_identity(candidate, session):
                        existing_pid = candidate
                except (ValueError, OSError):
                    pass

            if existing_pid is not None:
                pid = existing_pid
                recovered.append((session, pid))
                _write_lane_receipt(log_dir, session, role, pid, status="recovered")
            else:
                pid = _spawn_one(lane_sh, session, log_path, prompt_path, pidfile, env)
                newly_spawned.append((session, pid))
                _write_lane_receipt(log_dir, session, role, pid, status="active")

            if role == "supervisor":
                out["supervisor"] = pid
            else:
                lane_n += 1
                out[f"lane{lane_n}"] = pid
            _update_partial_manifest(log_dir, recovered + newly_spawned, out)
    except Exception:
        _rollback_launched(log_dir, newly_spawned)
        raise
    manifest = log_dir / "launch_manifest.json"
    manifest.write_text(
        json.dumps({"sessions": [s for s, _ in (recovered + newly_spawned)], "pids": out}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
