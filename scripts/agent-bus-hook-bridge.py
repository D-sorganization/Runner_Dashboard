#!/usr/bin/env python3
"""Bridge fleet provider hooks to the Dashboard agent bus (#2155 slice 2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _fleet_client():
    root = Path(__file__).resolve().parents[1]
    fleet_dir = root / "clients" / "fleet"
    if str(fleet_dir) not in sys.path:
        sys.path.insert(0, str(fleet_dir))
    from fleet_client import FleetClient  # noqa: PLC0415

    return FleetClient()


def session_start() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients" / "fleet"))
    import agent_bus_hooks  # noqa: PLC0415

    _ = sys.stdin.read()
    client = _fleet_client()
    if not client.session:
        return 0
    summary = agent_bus_hooks.session_start(client)
    print(json.dumps(summary))
    return 0


def pre_push() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients" / "fleet"))
    import agent_bus_hooks  # noqa: PLC0415

    agent_bus_hooks.pre_push()
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
