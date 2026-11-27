"""RD#4708 (FC-015a P1): disarm evaluation must precede the arm pre-filter.

Covers three defects in the merge shepherd:
1. ``plan_repo_disarms`` skipped every PR that the arm pre-filter rejects
   (CHANGES_REQUESTED, QUEUED/BLOCKED/UNSTABLE merge state, failed checks, holds),
   so a queued PR with a fresh FIX-NEEDED at head was never disarmed.
2. Bold ``**Independent Exact-Head Acceptance Review — FIX-NEEDED** @sha`` comments
   were not parsed into a head-bound rejection.
3. ``rank_candidates_for_shepherding`` ranked DISARM after SKIP.
"""

from __future__ import annotations

from coordination.merge_shepherd import (
    PRCandidate,
    ShepherdAction,
    rank_candidates_for_shepherding,
)
from coordination.merge_shepherd_exact_head import (
    collect_review_passes_from_comments,
    collect_review_passes_from_github_reviews,
)
from coordination.merge_shepherd_pass import plan_repo_disarms, plan_repo_pass

REPO = "D-sorganization/Runner_Dashboard"
HEAD = "d" * 40
AUTHOR = "d-sorgagyagent[bot]"
REVIEWER = "d-sorgcodexagent[bot]"


def _approve_review() -> dict:
    return {
        "state": "APPROVED",
        "commit": {"oid": HEAD},
        "author": {"login": REVIEWER},
        "submittedAt": "2026-11-21T17:10:00Z",
    }


def _bold_fix_needed_comment(sha: str = HEAD) -> dict:
    return {
        "body": f"**Independent Exact-Head Acceptance Review — FIX-NEEDED** @{sha}",
        "createdAt": "2026-11-21T17:29:05Z",
        "author": {"login": REVIEWER},
    }


def _queued_node(
    *,
    number: int = 15843,
    merge_state: str = "QUEUED",
    review_decision: str = "APPROVED",
    comments: list[dict] | None = None,
) -> dict:
    """A PR already in the merge queue (queue entry exists, auto-merge armed)."""
    return {
        "number": number,
        "title": f"pr {number}",
        "isDraft": False,
        "headRefOid": HEAD,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": merge_state,
        "reviewDecision": review_decision,
        "author": {"login": AUTHOR},
        "autoMergeRequest": {},
        "mergeQueueEntry": {"position": 1},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
        "labels": {"nodes": []},
        "files": {"pageInfo": {"hasNextPage": False}, "nodes": [{"path": "backend/x.py"}]},
        "reviews": {"nodes": [_approve_review()]},
        "comments": {"nodes": list(comments or [])},
    }


def test_disarm_planned_when_prefilter_would_skip_queued_pr() -> None:
    """QUEUED merge state + CHANGES_REQUESTED used to hit the pre-filter and never disarm."""
    node = _queued_node(
        merge_state="QUEUED",
        review_decision="CHANGES_REQUESTED",
        comments=[_bold_fix_needed_comment()],
    )
    disarms = plan_repo_disarms(REPO, [node], skip_list=frozenset(), holds=(), live=False)
    assert [(a.pr_number, a.kind, a.detail) for a in disarms] == [(15843, "disarm", "rejected_at_head")]


def test_disarm_planned_for_blocked_and_unstable_merge_states() -> None:
    for state in ("BLOCKED", "UNSTABLE"):
        node = _queued_node(merge_state=state, comments=[_bold_fix_needed_comment()])
        disarms = plan_repo_disarms(REPO, [node], skip_list=frozenset(), holds=(), live=False)
        assert len(disarms) == 1, state


def test_bold_fix_needed_comment_is_bound_to_head_as_rejection() -> None:
    passes = collect_review_passes_from_comments([_bold_fix_needed_comment()], 15843, author_login=AUTHOR)
    assert len(passes) == 1
    assert passes[0].head_sha == HEAD
    assert passes[0].verdict == "reject"


def test_bold_fix_needed_for_other_sha_does_not_disarm_current_head() -> None:
    other = "e" * 40
    node = _queued_node(comments=[_bold_fix_needed_comment(sha=other)])
    disarms = plan_repo_disarms(REPO, [node], skip_list=frozenset(), holds=(), live=False)
    assert disarms == []


def test_rank_puts_disarm_ahead_of_skip() -> None:
    disarming = PRCandidate(
        repo=REPO,
        pr_number=2,
        head_sha=HEAD,
        auto_merge_enabled=True,
        is_queued=True,
        checks_status="SUCCESS",
        review_passes=collect_review_passes_from_comments([_bold_fix_needed_comment()], 2, author_login=AUTHOR),
    )
    skipping = PRCandidate(repo=REPO, pr_number=1, head_sha=HEAD, checks_status="PENDING")
    decisions = rank_candidates_for_shepherding([skipping, disarming])
    assert [d.action for d in decisions] == [ShepherdAction.DISARM, ShepherdAction.SKIP]


def test_fix_needed_after_queue_entry_disarms_before_any_merge() -> None:
    """Queue entry first (approved, no verdict yet): nothing to disarm.

    A bold FIX-NEEDED at the same head then arrives. The shepherd must plan a
    disarm (dequeue) and no arm for that PR. Live dequeue execution is deferred
    under #2657/#2880, so this asserts the planned action.
    """
    before = _queued_node(comments=[])
    assert plan_repo_disarms(REPO, [before], skip_list=frozenset(), holds=(), live=False) == []

    after = _queued_node(comments=[_bold_fix_needed_comment()])
    disarms = plan_repo_disarms(REPO, [after], skip_list=frozenset(), holds=(), live=False)
    assert [(a.pr_number, a.detail) for a in disarms] == [(15843, "rejected_at_head")]

    arms, _skips, backoff = plan_repo_pass(
        REPO,
        [after],
        skip_list=frozenset(),
        holds=(),
        armed_count=0,
        queue_depth=0,
        live=False,
    )
    assert backoff is False
    assert arms == []


def test_github_review_path_still_recognises_approval_for_arm_regression_guard() -> None:
    """Guard: the approve pass used to arm stays head-bound (no regression from parser change)."""
    passes = collect_review_passes_from_github_reviews([_approve_review()], 15843, author_login=AUTHOR)
    assert [(p.head_sha, p.verdict) for p in passes] == [(HEAD, "approve")]
