#!/usr/bin/env python3
"""CLI entrypoint for the out-of-band hub liveness sentinel (#3050).

Wires the probe + state-machine core (``hub_sentinel.py``, #3050 slice 1)
to the alert sinks and dispatch fan-out (``hub_sentinel_alerts.py``,
#3050 slice 2). This is the script the ``hub-sentinel.service`` systemd
unit actually runs; pure stdlib and self-contained like every other
``deploy/*.py`` entrypoint, since the hub must never need its own backend
venv to report its own outage.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from hub_sentinel import SentinelEvent, default_state_path, evaluate_probe, load_state, probe_hub, save_state
from hub_sentinel_alerts import AlertSink, build_sinks_from_env, dispatch


def run_once(
    *,
    hub_url: str,
    state_path: Path,
    sinks: list[AlertSink],
    dry_run: bool,
    now: datetime | None = None,
) -> list[SentinelEvent]:
    moment = now or datetime.now(UTC)
    classification, detail = probe_hub(hub_url)
    state = load_state(state_path)
    new_state, events = evaluate_probe(state, classification, detail, now=moment)
    if not dry_run:
        save_state(state_path, new_state)
    for event in events:
        if dry_run:
            print(f"[dry-run] would send: {event.kind}: {event.message}")  # noqa: T201
        else:
            dispatch(sinks, event.message)
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Out-of-band hub liveness sentinel")
    parser.add_argument("--hub-url", default=os.environ.get("HUB_URL", "http://deskcomputer:8321"))
    parser.add_argument("--state-file", default=str(default_state_path()))
    parser.add_argument("--dry-run", action="store_true", help="probe and print, but never persist state or alert")
    args = parser.parse_args(argv)
    sinks: list[AlertSink] = [] if args.dry_run else build_sinks_from_env(dict(os.environ))
    events = run_once(
        hub_url=args.hub_url,
        state_path=Path(args.state_file).expanduser(),
        sinks=sinks,
        dry_run=args.dry_run,
    )
    for event in events:
        print(f"{event.kind}: {event.message}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
