#!/usr/bin/env python3
"""Bridge fleet provider hooks to the Dashboard agent bus (#2155 slice 2, #2982)."""

from __future__ import annotations

import json
import sys
from pathlib import Path


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
    if args[0] == "session-start":
        return session_start()
    if args[0] == "pre-push":
        return pre_push()
    print(f"unknown mode: {args[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
