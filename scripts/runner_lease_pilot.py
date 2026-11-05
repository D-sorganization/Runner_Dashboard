#!/usr/bin/env python3
"""Collect #3878 staging pilot evidence for the runner-lease YAML->SQLite cutover.

Read-only for the node's live ``leases.yml``: the pilot runs on a copy under
``--work-dir`` (default ``<config-dir>/lease-pilot``) and prints a JSON report
of counts and verdicts. Exit code 0 when the pilot passed, 1 otherwise.

    python scripts/runner_lease_pilot.py --config-dir ~/.config/runner-dashboard
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from runner_lease import _default_config_dir  # noqa: E402
from runner_lease_pilot import run_pilot  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config-dir", type=Path, default=_default_config_dir(), help="node config dir holding leases.yml"
    )
    parser.add_argument(
        "--work-dir", type=Path, help="scratch dir for the copy and SQLite db (default <config-dir>/lease-pilot)"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    config_dir = args.config_dir.expanduser()
    work_dir = (args.work_dir or config_dir / "lease-pilot").expanduser()
    report = run_pilot(config_dir, work_dir)
    sys.stdout.write(json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
