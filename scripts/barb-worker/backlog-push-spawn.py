#!/usr/bin/env python3
"""Spawn backlog-push lanes detached from the panel-relay oneshot (#4098).

Reads a JSON plan array on stdin; prints a JSON object of role->pid on stdout.
Never prints tokens. Unsets GH_TOKEN/GITHUB_TOKEN so lanes use the hub fleet gh wrapper.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


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


def _write_lane_receipt(log_dir: Path, session: str, role: str, pid: int, status: str = "active") -> Path:
    """Durable per-lane ownership receipt written immediately upon launch (#4103)."""
    receipt_path = log_dir / f"{session}.receipt.json"
    data = {
        "session": session,
        "role": role,
        "pid": pid,
        "status": status,
        "updated_at": time.time(),
    }
    receipt_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return receipt_path


def _update_partial_manifest(log_dir: Path, launched: list[tuple[str, int]], pids: dict[str, int]) -> Path:
    """Update recoverable partial-active record as lanes launch (#4103)."""
    partial_path = log_dir / "partial_active.json"
    data = {
        "sessions": [s for s, _ in launched],
        "pids": pids,
        "updated_at": time.time(),
    }
    partial_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return partial_path


def _rollback_launched(log_dir: Path, launched: list[tuple[str, int]]) -> None:
    """Stop only workers recorded for this spawn attempt (#4103)."""
    cleanup_failures: dict[str, dict[str, Any]] = {}
    for session, pid in reversed(launched):
        if pid <= 1:
            # Never target PID 0, 1, or negative
            continue
        killed = False
        try:
            os.kill(pid, signal.SIGTERM)
            killed = True
        except OSError as exc:
            import errno

            if getattr(exc, "errno", None) == errno.ESRCH:
                killed = True
            else:
                cleanup_failures[session] = {
                    "pid": pid,
                    "error": str(exc),
                    "timestamp": time.time(),
                }
        receipt_path = log_dir / f"{session}.receipt.json"
        if killed:
            if receipt_path.is_file():
                try:
                    rec = json.loads(receipt_path.read_text(encoding="utf-8"))
                    rec["status"] = "rolled_back"
                    rec["rolled_back_at"] = time.time()
                    receipt_path.write_text(json.dumps(rec, indent=2, sort_keys=True), encoding="utf-8")
                except Exception:
                    pass
            (log_dir / f"{session}.pid").unlink(missing_ok=True)
            (log_dir / f"{session}.exit").unlink(missing_ok=True)

    if cleanup_failures:
        evidence = log_dir / "recovery_evidence.json"
        evidence.write_text(
            json.dumps(
                {"cleanup_failures": cleanup_failures, "timestamp": time.time()},
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


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
            [
                "setsid",
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
    launched: list[tuple[str, int]] = []
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

            # Check if this session is already running from a prior attempt / recovery
            existing_pid: int | None = None
            if pidfile.is_file() and pidfile.stat().st_size > 0:
                try:
                    candidate = int(pidfile.read_text(encoding="utf-8").strip())
                    if _is_active_process(candidate):
                        existing_pid = candidate
                except (ValueError, OSError):
                    pass

            if existing_pid is not None:
                pid = existing_pid
                _write_lane_receipt(log_dir, session, role, pid, status="recovered")
            else:
                pid = _spawn_one(lane_sh, session, log_path, prompt_path, pidfile, env)
                _write_lane_receipt(log_dir, session, role, pid, status="active")

            launched.append((session, pid))
            if role == "supervisor":
                out["supervisor"] = pid
            else:
                lane_n += 1
                out[f"lane{lane_n}"] = pid
            _update_partial_manifest(log_dir, launched, out)
    except Exception:
        _rollback_launched(log_dir, launched)
        raise
    manifest = log_dir / "launch_manifest.json"
    manifest.write_text(
        json.dumps({"sessions": [s for s, _ in launched], "pids": out}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
