#!/usr/bin/env python3
"""Evaluate slot-health flags from a jobs JSON snapshot (#2898, read-only)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from slot_health import (  # noqa: E402
    SlotHealthPreconditionError,
    analyze_jobs,
    load_jobs,
    publish_slot_health_flags,
)

LOG = logging.getLogger("slot_health")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect unhealthy runner slots from job history.")
    parser.add_argument("--jobs", type=Path, required=True, help="JSON array of completed job records")
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--publish", action="store_true", help="Record hub fleet events and Barb operator lane audit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        report = analyze_jobs(load_jobs(args.jobs), window_hours=args.window_hours)
    except SlotHealthPreconditionError as exc:
        LOG.error("::error::%s", exc)
        return 2
    if args.publish and report["flags"]:
        publish_slot_health_flags(report["flags"])
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
