"""CLI: scheduled 5 PM fleet productivity data pull (RD#3641). Read-only; one
batched GraphQL call across every fleet repository."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from staff.fleet_productivity_pull import FLEET_REPOS, ProductivitySnapshot, pull_snapshot  # noqa: E402

_RUNNER = Callable[..., subprocess.CompletedProcess[str]]


def main(argv: Sequence[str] | None = None, *, run: _RUNNER = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repos", help="Comma-separated owner/name overrides; default is the fleet repo set.")
    args = parser.parse_args(argv)
    repos = [r.strip() for r in args.repos.split(",")] if args.repos else list(FLEET_REPOS)
    snapshot: ProductivitySnapshot = pull_snapshot(repos, run=run)
    print(json.dumps(snapshot, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
