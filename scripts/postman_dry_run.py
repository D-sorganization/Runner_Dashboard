#!/usr/bin/env python3
"""Run the postman in dry run: plan what it would deliver from the coordination board, send nothing.

  python scripts/postman_dry_run.py              one cycle, summary on stdout
  python scripts/postman_dry_run.py --json       the full report as JSON
  python scripts/postman_dry_run.py --every 300  repeat every 300 s until stopped

Reports are kept in ``$POSTMAN_DATA_DIR`` (default ``backend/data``) and served read-only at
``GET /api/coordination/postman/reports``. Needs a Repository_Management checkout (``STAFF_RM_ROOT``) and
``gh`` read access to the board issue. There is no live mode here; see ``docs/agents/messenger-agents.md``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from coordination.postman_runner import run_dry_cycle  # noqa: E402

MIN_EVERY = 60


def summary(entry: dict[str, Any]) -> str:
    """One line per planned action, after a header; an error replaces the actions."""
    head = f"{entry['at']}  dry run  board #{entry['board_issue']}"
    if "error" in entry:
        return f"{head}  error: {entry['error']}"
    counts = ", ".join(f"{k}={v}" for k, v in sorted(entry["counts"].items())) or "nothing to do"
    rows = [f"  {a['kind']:<10} {a['detail']}  {a['key'][:40]}" for a in entry["actions"]]
    return "\n".join([f"{head}  {counts}", *rows])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument("--every", type=int, default=0, help=f"repeat every N seconds (minimum {MIN_EVERY})")
    args = parser.parse_args(argv)
    if args.every and args.every < MIN_EVERY:
        parser.error(f"--every must be at least {MIN_EVERY} seconds")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    while True:
        entry = run_dry_cycle()
        print(json.dumps(entry, indent=2) if args.json else summary(entry), flush=True)
        if not args.every:
            return 1 if "error" in entry else 0
        time.sleep(args.every)


if __name__ == "__main__":
    raise SystemExit(main())
