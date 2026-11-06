"""Pre-push / pre-PR size gate: fail a diff over the line cap (#3963 S1).

WHY THIS EXISTS
----------------
60 PRs across the fleet went over the 500-line cap in 8 days
(2026-10-27..11-03): claude 23, cursor 15, fleet-bot lanes 10, gemini 5, agy 3,
codex 2. Size was only ever caught at review/CI time, after the work was
already spent. This gate counts the diff against ``main`` *before* push,
including generated files and lockfiles (unlike the informal "hand-written
lines" convention lanes have used so far), so an over-size change is caught
immediately rather than at review.

Exceptions are not a flag an agent can pass itself: they come only from an
explicit per-PR allowlist file that Dieter sets (``--allowlist``), never from
``--no-verify`` or similar bypasses.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Injected in tests so this module runs hermetically — no real git process.
CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]

DEFAULT_LIMIT = 500
DEFAULT_ALLOWLIST_NAME = "config/pr_size_allowlist.json"


@dataclass(frozen=True)
class SizeVerdict:
    """The result of checking one diff against the size cap."""

    within_limit: bool
    total_lines: int
    reason: str


def parse_numstat(numstat_output: str) -> int:
    """Sum added+deleted lines from ``git diff --numstat`` output.

    Binary files report ``-`` for both counts; those lines are skipped since
    there is no meaningful line count to add (they still show up in the diff,
    just not as a line-count contribution).
    """
    total = 0
    for line in numstat_output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        added, deleted = parts[0], parts[1]
        if added == "-" or deleted == "-":
            continue
        total += int(added) + int(deleted)
    return total


def _run_git(argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=True)


def count_changed_lines(
    repo_root: Path, merge_base: str, run: CommandRunner = _run_git
) -> int:
    """Count added+deleted lines in ``HEAD`` vs. its merge-base with ``merge_base``.

    Triple-dot diff (``merge_base...HEAD``) compares against the merge base
    rather than the tip of ``merge_base``, so commits landed on ``main`` after
    the branch forked don't count against the branch's own diff.
    """
    result = run(["git", "diff", "--numstat", f"{merge_base}...HEAD"], repo_root)
    return parse_numstat(result.stdout)


def resolve_diff_merge_base(
    repo_root: Path,
    merge_base: str,
    merge_base_ref: str | None,
    run: CommandRunner = _run_git,
) -> str:
    """Ref to diff against for size counting.

    Default ``merge_base`` matches the local pre-push hook: the fork point with
    ``origin/main`` (triple-dot semantics in :func:`count_changed_lines`).

    In CI, stacked PRs target another head branch; pass ``merge_base_ref`` as
    the PR base SHA so we use ``git merge-base(PR_BASE, HEAD)`` — the first
    parent chain anchors incremental size on the stack instead of cumulating
    against ``main``.
    """
    if merge_base_ref is None:
        return merge_base
    result = run(["git", "merge-base", merge_base_ref, "HEAD"], repo_root)
    sha = result.stdout.strip()
    if not sha:
        raise RuntimeError("git merge-base returned empty output")
    return sha


def _load_allowlist(allowlist_path: Path) -> frozenset[int]:
    if not allowlist_path.exists():
        return frozenset()
    data = json.loads(allowlist_path.read_text(encoding="utf-8"))
    return frozenset(int(pr) for pr in data.get("allowed_prs", []))


def evaluate_size(
    repo_root: Path,
    *,
    merge_base: str,
    limit: int,
    pr_number: int | None,
    allowlist_path: Path,
    run: CommandRunner = _run_git,
) -> SizeVerdict:
    """Check whether the current branch's diff against ``merge_base`` fits the cap."""
    total_lines = count_changed_lines(repo_root, merge_base, run=run)
    if total_lines <= limit:
        return SizeVerdict(within_limit=True, total_lines=total_lines, reason=f"{total_lines} <= {limit}")

    if pr_number is not None and pr_number in _load_allowlist(allowlist_path):
        return SizeVerdict(
            within_limit=True,
            total_lines=total_lines,
            reason=f"{total_lines} > {limit} but PR #{pr_number} is allowlisted",
        )

    return SizeVerdict(within_limit=False, total_lines=total_lines, reason=f"{total_lines} > {limit}")


def main(argv: list[str] | None = None) -> int:
    """CLI: ``pr_size_gate.py [--repo-root .] [--merge-base origin/main] [--limit 500] [--pr N] [--allowlist path]``.

    Exit 0 if the diff is within the cap (or the PR is allowlisted), exit 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", default=".", help="repository root (default: cwd)")
    parser.add_argument("--merge-base", default="origin/main")
    parser.add_argument(
        "--merge-base-ref",
        default=None,
        metavar="SHA",
        help=(
            "PR base commit (e.g. github.event.pull_request.base.sha); when set, "
            "count the diff from merge-base(SHA, HEAD) to HEAD instead of using "
            "--merge-base alone (stacks vs main-only PRs)"
        ),
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--pr", type=int, default=None, help="PR number, for allowlist lookup")
    parser.add_argument(
        "--allowlist",
        default=None,
        help=f"path to the allowlist JSON (default: <repo-root>/{DEFAULT_ALLOWLIST_NAME})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    repo_root = Path(args.repo_root)
    allowlist_path = Path(args.allowlist) if args.allowlist else repo_root / DEFAULT_ALLOWLIST_NAME

    diff_base = resolve_diff_merge_base(
        repo_root, args.merge_base, args.merge_base_ref, run=_run_git
    )

    verdict = evaluate_size(
        repo_root,
        merge_base=diff_base,
        limit=args.limit,
        pr_number=args.pr,
        allowlist_path=allowlist_path,
        run=_run_git,
    )
    print(verdict.reason)
    if not verdict.within_limit:
        logger.error(
            "size gate: %s changed lines exceeds the %s-line cap; split into a first "
            "slice and file a follow-up issue for the rest",
            verdict.total_lines,
            args.limit,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
