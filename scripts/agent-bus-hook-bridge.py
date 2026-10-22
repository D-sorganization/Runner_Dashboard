#!/usr/bin/env python3
"""Bridge fleet provider hooks to the Dashboard agent bus (#2155 slice 2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _paths() -> None:
    root = Path(__file__).resolve().parents[1]
    backend = root / "backend"
    fleet = root / "clients" / "fleet"
    for p in (backend, fleet):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


def session_start() -> int:
    _paths()
    from agent_bus.session_hooks import session_start as run_start  # noqa: PLC0415
    from fleet_client import FleetClient  # noqa: PLC0415

    _ = sys.stdin.read()
    client = FleetClient()
    if not client.session:
        return 0
    summary = run_start(client)
    print(json.dumps(summary))
    return 0


def pre_push() -> int:
    _paths()
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
