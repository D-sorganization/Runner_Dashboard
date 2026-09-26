#!/usr/bin/env python3
"""Claude Code status-line command that records the plan's rate-limit windows (#1587).

Claude Code pipes a JSON payload to its ``statusLine`` command on every refresh,
including ``rate_limits.{five_hour,seven_day}.{used_percentage,resets_at}`` on
subscription plans. This script stores those windows in the staff quota file the
Runner Dashboard reads (``GET /api/staff/quota``) and prints a short summary, so
interactive sessions keep the dashboard's view of the Claude plan current at no cost.

Configure in the settings of the account the dashboard uses::

    "statusLine": {"type": "command",
                   "command": "python3 /path/to/Runner_Dashboard/scripts/claude_statusline_quota.py"}

A status line must never break the session: bad input prints nothing and exits 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from staff import quota  # noqa: E402

_SHORT = {"five_hour": "5h", "seven_day": "7d"}


def main(argv: Sequence[str] | None = None, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", type=Path, default=None, help="quota state file (default: STAFF_QUOTA_STATE)")
    args = parser.parse_args(argv)
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        payload = json.loads(stdin.read() or "null")
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    snapshot = quota.from_claude_statusline(payload, observed_at=datetime.now(UTC))
    if snapshot is None:
        return 0
    try:
        quota.QuotaStore(args.store).record(snapshot)
    except OSError:
        pass  # a read-only home must not blank the status line
    parts = [f"{_SHORT.get(w.name, w.name)} {w.used_percent:g}%" for w in snapshot.windows]
    print(" · ".join(parts), file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
