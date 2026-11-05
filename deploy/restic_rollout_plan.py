#!/usr/bin/env python3
"""CLI for fleet restic rollout planning (#4012). Default dry-run."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "backend"))

from fleet_restic_rollout import plan_restic_rollout  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or apply fleet restic host rollout (repo-only hooks).")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Record install steps (requires FLEET_RESTIC_PILOT_COMPLETE=1). Does not run restic.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Override config/fleet_restic_hosts.json (tests).",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = plan_restic_rollout(config_path=args.config, apply=bool(args.apply))
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0 if report["pilot_ready"] or report["warnings"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
