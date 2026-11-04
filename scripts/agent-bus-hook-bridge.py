#!/usr/bin/env python3
"""Bridge fleet provider hooks to the Dashboard agent bus (#2155 slice 2, #2982)."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

MIN_PYTHON = (3, 11)
REEXEC_ENV = "AGENT_BUS_HOOK_REEXEC"
_NEWEST_MINOR = 20


def _find_supported_python() -> str | None:
    for minor in range(_NEWEST_MINOR, MIN_PYTHON[1] - 1, -1):
        path = shutil.which(f"python3.{minor}")
        if path:
            return path
    return None


def ensure_supported_python(args: Sequence[str], version_info: Sequence[int] = sys.version_info) -> int | None:
    """Return None to proceed, or an exit code when the hook must stop (#3980).

    The backend needs Python 3.11+ (``datetime.UTC``), but hooks run bare
    ``python3``. Re-exec once under the newest ``python3.1x`` on PATH; with
    none available, fail open with one stderr line instead of a traceback.
    """
    if tuple(version_info[:2]) >= MIN_PYTHON:
        return None
    if os.environ.get(REEXEC_ENV) != "1":
        candidate = _find_supported_python()
        if candidate:
            os.environ[REEXEC_ENV] = "1"
            os.execv(candidate, [candidate, str(Path(__file__).resolve()), *args])
    found = ".".join(str(part) for part in version_info[:2])
    print(f"agent-bus hook skipped: needs Python 3.11+, found {found} (#3980)", file=sys.stderr)
    return 0


def _paths() -> Path | None:
    fleet_dir = Path(__file__).resolve().parents[1] / "clients" / "fleet"
    if str(fleet_dir) not in sys.path:
        sys.path.insert(0, str(fleet_dir))
    from dashboard_root import resolve_dashboard_root  # noqa: PLC0415

    root = resolve_dashboard_root(start=Path.cwd())
    if root is None:
        return None
    backend = root / "backend"
    fleet = root / "clients" / "fleet"
    for p in (backend, fleet):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root


def session_start() -> int:
    root = _paths()
    if root is None:
        return 0
    from fleet_client import FleetClient  # noqa: PLC0415
    from fleet_provider_identity import infer_fleet_agent, infer_fleet_session  # noqa: PLC0415
    from agent_bus.session_hooks import session_start as run_start  # noqa: PLC0415

    raw = sys.stdin.read()
    agent = infer_fleet_agent()
    if not agent:
        return 0
    session = infer_fleet_session(agent=agent, hook_stdin=raw)
    client = FleetClient(agent=agent, session=session)
    summary = run_start(client)
    print(json.dumps(summary))
    return 0


def pre_push() -> int:
    if _paths() is None:
        return 0
    from agent_bus.session_hooks import pre_push as run_pre_push  # noqa: PLC0415

    run_pre_push()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: agent-bus-hook-bridge.py <session-start|pre-push>", file=sys.stderr)
        return 2
    stop = ensure_supported_python(args)
    if stop is not None:
        return stop
    modes = {"session-start": session_start, "pre-push": pre_push}
    if args[0] not in modes:
        print(f"unknown mode: {args[0]}", file=sys.stderr)
        return 2
    try:
        return modes[args[0]]()
    except ImportError as exc:
        print(f"agent-bus hook skipped: backend dependencies unavailable ({exc}) (#3980)", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
