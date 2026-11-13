#!/usr/bin/env python3
"""Start the dashboard backend for Playwright e2e in a hermetic sandbox (#2943).

On self-hosted runners the backend otherwise reads the runner user's real
``~/.config`` (session secret, machine registry), derives a fleet from it and
takes a shared ``/tmp`` leader lock.  This launcher gives it an isolated HOME,
no fleet derivation, a private leader lock, a 90 s readiness budget and dumps
the backend log tail when it never becomes ready.

Usage: ``start_e2e_backend.py --port N --root DIR --pid-file FILE``
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

READY_TIMEOUT_S = 90
LOG_TAIL_LINES = 80
REPO_ROOT = Path(__file__).resolve().parent.parent

# Variables that would point the backend at real fleet state.
_DROPPED = ("FLEET_NODES", "MACHINE_REGISTRY_PATH", "HUB_URL", "MACHINE_ROLE")


def build_backend_env(base: Mapping[str, str], root: Path, *, port: int) -> dict[str, str]:
    """Return the isolated environment for the e2e backend (pure apart from mkdir)."""
    if not 0 < port < 65536:
        raise ValueError(f"invalid port: {port}")
    home = root / "home"
    config = home / ".config"
    cache = home / ".cache"
    for directory in (config, cache):
        directory.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in base.items() if k not in _DROPPED}
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(config),
            "XDG_CACHE_HOME": str(cache),
            "RUNNER_DASHBOARD_SESSION_SECRET_DIR": str(config / "runner-dashboard"),
            "AUTODERIVE_FLEET_NODES": "0",
            "DASHBOARD_LEADER_LOCK_PATH": str(root / "dashboard-leader.lock"),
            "GH_TOKEN": "dummy",
            "DASHBOARD_PORT": str(port),
            "DASHBOARD_LOOPBACK_AUTH": "1",
            "DASHBOARD_HOST": "127.0.0.1",  # loopback auth refuses a non-loopback bind (#3617, #3625)
        }
    )
    return env


def tail_log(path: Path, lines: int = LOG_TAIL_LINES) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"<no backend log at {path}>"
    return "\n".join(text.splitlines()[-lines:])


def _http_probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 - loopback URL
            return 200 <= resp.status < 300
    except OSError:
        return False


def wait_ready(
    *,
    port: int,
    log_path: Path,
    timeout_s: int = READY_TIMEOUT_S,
    probe: Callable[[str], bool] = _http_probe,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll /api/health once per second; on failure print the backend log tail."""
    url = f"http://127.0.0.1:{port}/api/health"
    for attempt in range(1, max(timeout_s, 1) + 1):
        if probe(url):
            print(f"Backend ready after {attempt}s")
            return True
        sleep(1)
    print(f"Backend did not become ready on port {port} after {timeout_s}s", file=sys.stderr)
    print(f"--- last {LOG_TAIL_LINES} lines of backend log ---", file=sys.stderr)
    print(tail_log(log_path), file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--root", type=Path, required=True, help="job temp dir for HOME/log/lock")
    parser.add_argument("--pid-file", type=Path, required=True)
    args = parser.parse_args(argv)

    args.root.mkdir(parents=True, exist_ok=True)
    env = build_backend_env(os.environ, args.root, port=args.port)
    log_path = args.root / "dashboard-backend.log"
    with log_path.open("ab") as log:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv
            [sys.executable, "server.py"],
            cwd=REPO_ROOT / "backend",
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    args.pid_file.write_text(str(proc.pid), encoding="utf-8")
    if wait_ready(port=args.port, log_path=log_path):
        return 0
    proc.terminate()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
