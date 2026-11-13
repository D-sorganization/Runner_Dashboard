#!/usr/bin/env python3
"""CLI for agent repo worktree hygiene (default dry-run)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "backend"))

from agent_worktree_hygiene import (  # noqa: E402
    DEFAULT_MIN_AGE_HOURS,
    DEFAULT_PROTECTED_WORKTREES,
    discover_repo_checkouts,
    plan_agent_worktree_prune,
)

# Some checkouts were skipped (git timed out); every other checkout was processed normally.
EXIT_PARTIAL = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prune stale agent git worktrees under fleet repo checkouts.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Remove eligible worktrees (default is dry-run logging only).",
    )
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=DEFAULT_MIN_AGE_HOURS,
        help=f"Minimum worktree directory age (default {DEFAULT_MIN_AGE_HOURS}).",
    )
    parser.add_argument(
        "--repo-root",
        action="append",
        default=[],
        help="Additional directory to scan for repo checkouts (repeatable).",
    )
    parser.add_argument(
        "--protect",
        action="append",
        default=[],
        help="Extra protected worktree basename tokens (repeatable).",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    apply = bool(args.apply)
    if os.environ.get("AGENT_WORKTREE_HYGIENE_APPLY", "").strip().lower() in {"1", "true", "yes"}:
        apply = True
    extra = [Path(p).expanduser() for p in args.repo_root]
    protected = DEFAULT_PROTECTED_WORKTREES + tuple(args.protect)
    checkouts = discover_repo_checkouts(extra)
    report = plan_agent_worktree_prune(
        checkouts,
        apply=apply,
        min_age_hours=float(args.min_age_hours),
        protected=protected,
    )
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return EXIT_PARTIAL if report["skipped_checkouts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
