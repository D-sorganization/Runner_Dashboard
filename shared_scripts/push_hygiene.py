"""Push-hygiene guard for pull_request synchronize and hub pre-push (issue #2892).

Refuses empty-tree (``--allow-empty``) and main-sync merge (``git merge origin/main``)
commits on PRs that have auto-merge enabled or sit in the merge queue.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

COMMENT_MARKER = "<!-- push-hygiene-guard v1 -->"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class CommitInfo:
    """Resolved git commit metadata for push-hygiene evaluation."""

    sha: str
    tree_sha: str
    parent_shas: tuple[str, ...]
    parent_tree_shas: tuple[str, ...]
    second_parent_is_main: bool = False
    message: str = ""


@dataclass(frozen=True)
class PullRequestQueueState:
    """Live pull request auto-merge and merge-queue state."""

    pr_number: int | None = None
    auto_merge_enabled: bool = False
    in_merge_queue: bool = False
    state: str = "OPEN"

    @property
    def is_armed_or_queued(self) -> bool:
        return self.state.strip().upper() == "OPEN" and (self.auto_merge_enabled or self.in_merge_queue)


@dataclass(frozen=True)
class PushHygieneDecision:
    """Outcome of checking a commit against a PR's armed/queued state."""

    allowed: bool
    violation: str = ""
    reason: str = ""


def _git_out(repo_root: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo_root, text=True, capture_output=True, check=True, timeout=30)
    return res.stdout.strip()


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    res = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    return res.returncode == 0


def _resolve_main_ref(repo_root: Path, preferred: str = "origin/main") -> str | None:
    for candidate in (preferred, "origin/main", "refs/remotes/origin/main", "main", "refs/heads/main"):
        res = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    return None


def inspect_git_commit(repo_root: Path, rev: str = "HEAD", *, main_ref: str = "origin/main") -> CommitInfo:
    """Inspect ``rev`` in ``repo_root`` and return its :class:`CommitInfo`."""
    if not rev or not rev.strip():
        raise ValueError("rev must be a non-empty git revision")
    root = repo_root.resolve(strict=True)
    sha = _git_out(root, "rev-parse", f"{rev}^{{commit}}")
    tree_sha = _git_out(root, "rev-parse", f"{sha}^{{tree}}")
    parent_shas = tuple(p for p in _git_out(root, "log", "-1", "--format=%P", sha).split() if p)
    parent_tree_shas = tuple(_git_out(root, "rev-parse", f"{p}^{{tree}}") for p in parent_shas)
    message = _git_out(root, "log", "-1", "--format=%B", sha)
    second_parent_is_main = False
    if len(parent_shas) >= 2 and (main_sha := _resolve_main_ref(root, preferred=main_ref)) is not None:
        second_parent_is_main = parent_shas[1] == main_sha or _is_ancestor(root, parent_shas[1], main_sha)
    return CommitInfo(sha, tree_sha, parent_shas, parent_tree_shas, second_parent_is_main, message)


def classify_commit(commit: CommitInfo) -> tuple[str, str]:
    """Return ``(violation_code, detail)`` or ``("", "")`` when the commit is clean."""
    if not commit.sha or not commit.tree_sha:
        raise ValueError("commit sha and tree_sha must be non-empty")
    if len(commit.parent_tree_shas) != len(commit.parent_shas):
        raise ValueError("parent_tree_shas length must match parent_shas length")
    if len(commit.parent_shas) >= 2 and commit.second_parent_is_main:
        return (
            "main_sync_merge",
            f"commit {commit.sha[:12]} is a main-sync merge (second parent {commit.parent_shas[1][:12]} is on main)",
        )
    if len(commit.parent_shas) == 1 and commit.tree_sha == commit.parent_tree_shas[0]:
        return (
            "empty_commit",
            f"commit {commit.sha[:12]} has same tree ({commit.tree_sha[:12]}) as parent {commit.parent_shas[0][:12]}",
        )
    return ("", "")


def evaluate_push_hygiene(commit: CommitInfo, pr_state: PullRequestQueueState) -> PushHygieneDecision:
    """Evaluate whether ``commit`` is permitted given ``pr_state``."""
    violation, detail = classify_commit(commit)
    if not pr_state.is_armed_or_queued:
        return PushHygieneDecision(allowed=True, reason="PR is neither armed nor in the merge queue")
    if not violation:
        return PushHygieneDecision(allowed=True)
    labels = [
        lbl
        for flag, lbl in (
            (pr_state.auto_merge_enabled, "auto-merge enabled"),
            (pr_state.in_merge_queue, "in merge queue"),
        )
        if flag
    ]
    pr_label = f"PR #{pr_state.pr_number}" if pr_state.pr_number is not None else "branch PR"
    reason = f"{violation} refused on {pr_label} ({' and '.join(labels)}): {detail}"
    decision = PushHygieneDecision(allowed=False, violation=violation, reason=reason)
    assert not decision.allowed and decision.violation in ("empty_commit", "main_sync_merge") and decision.reason
    return decision


def evaluate_push_hygiene_with_recheck(
    commit: CommitInfo,
    initial_state: PullRequestQueueState,
    refresh_state: Callable[[], PullRequestQueueState] | None = None,
) -> PushHygieneDecision:
    """Evaluate push hygiene, re-checking live PR state for mid-push queue/arm transitions."""
    return evaluate_push_hygiene(commit, refresh_state() if refresh_state is not None else initial_state)


def format_hygiene_comment(decision: PushHygieneDecision, *, head_sha: str) -> str:
    """Format an idempotent PR comment explaining why push-hygiene failed."""
    if decision.allowed:
        raise ValueError("cannot format failure comment for an allowed push-hygiene decision")
    short_sha = (head_sha or "")[:12] or "unknown"
    return (
        f"{COMMENT_MARKER}\n"
        f"### Push Hygiene Guard (`{decision.violation}`)\n\n"
        f"Head `{short_sha}` was rejected because {decision.reason}.\n"
    )


def should_post_hygiene_comment(existing_comments: Sequence[str], *, head_sha: str = "") -> bool:
    """Return True only if no push-hygiene comment has been posted on the PR yet."""
    _ = head_sha
    return not any(COMMENT_MARKER in (body or "") for body in existing_comments)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate F4 push-hygiene guard")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--rev", default="HEAD")
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--pr-number", type=int, default=None)
    parser.add_argument(
        "--auto-merge-enabled",
        action="store_true",
        default=_env_truthy("STAFF_PR_AUTO_MERGE") or _env_truthy("PR_AUTO_MERGE_ENABLED"),
    )
    parser.add_argument(
        "--in-merge-queue",
        action="store_true",
        default=_env_truthy("STAFF_PR_IN_MERGE_QUEUE") or _env_truthy("PR_IN_MERGE_QUEUE"),
    )
    parser.add_argument("--pr-state", default=os.environ.get("PR_STATE", "OPEN"))
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        commit = inspect_git_commit(args.repo_root, args.rev, main_ref=args.main_ref)
        state = PullRequestQueueState(
            args.pr_number, bool(args.auto_merge_enabled), bool(args.in_merge_queue), args.pr_state
        )
        decision = evaluate_push_hygiene(commit, state)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        sys.stderr.write(f"::error::push-hygiene evaluation failed: {exc}\n")
        return 2
    if not decision.allowed:
        sys.stderr.write(f"::error::{decision.reason}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
