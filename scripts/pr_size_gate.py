"""Pre-push / pre-PR size advisory: report diff line count vs a reference cap (#3963).

Counts the diff (added + deleted from ``git diff --numstat``) against the merge
base with ``main``, including generated files and lockfiles — the same number
CI mirrors. **This never fails the process by default**; it logs a warning when
the count exceeds ``--limit`` so lanes get immediate feedback without blocking
push or merge. Prefer small, cohesive PRs; do **not** split a cohesive fix only
to stay under a line count.

Use ``--strict`` for enforcement (exit 1 when over the cap and not
allowlisted), e.g. optional local policy or tests.

``config/pr_size_allowlist.json`` remains supported for tagging allowlisted PRs
in messages; it does not gate merge.
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
    """The result of checking one diff against the advisory cap."""

    within_limit: bool
    total_lines: int
    reason: str
    allowlisted: bool = False


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
            allowlisted=True,
        )

    return SizeVerdict(within_limit=False, total_lines=total_lines, reason=f"{total_lines} > {limit}")


def main(argv: list[str] | None = None) -> int:
    """CLI: ``pr_size_gate.py [--repo-root .] [--merge-base origin/main] [--limit 500] [--pr N] [--allowlist path] [--strict]``.

    Default: always exit 0; log a warning when over the cap (unless allowlisted).
    With ``--strict``, exit 1 when over the cap and not allowlisted.
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
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when changed lines exceed --limit and the PR is not allowlisted",
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
    if verdict.within_limit:
        return 0
    msg = (
        f"PR size advisory: {verdict.total_lines} changed lines (reference cap {args.limit}). "
        "Prefer small, cohesive PRs; do not split a cohesive fix only to hit a line count."
    )
    logger.warning("WARNING: %s", msg)
    if args.strict:
        logger.error(
            "size gate (strict): %s changed lines exceeds the %s-line cap",
            verdict.total_lines,
            args.limit,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
