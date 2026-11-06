#!/usr/bin/env python3
"""Spawn backlog-push lanes detached from the panel-relay oneshot (#4098).

Reads a JSON plan array on stdin; prints a JSON object of role->pid on stdout.
Never prints tokens. Unsets GH_TOKEN/GITHUB_TOKEN so lanes use the hub fleet gh wrapper.
"""

from __future__ import annotations

import json
import os
import shutil
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
    lane_n = 0
    for entry in plan:
        role = str(entry["role"])
        session = str(entry["session"])
        prompt_path = log_dir / f"{session}.prompt"
        prompt_path.write_text(str(entry["prompt"]), encoding="utf-8")
        log_path = log_dir / f"{session}.log"
        log_path.touch()
        pidfile = log_dir / f"{session}.pid"
        pid = _spawn_one(lane_sh, session, log_path, prompt_path, pidfile, env)
        if role == "supervisor":
            out["supervisor"] = pid
        else:
            lane_n += 1
            out[f"lane{lane_n}"] = pid
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
