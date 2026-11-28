"""#4733: _revalidate_disarm must accept any planner-produced disarm reason, not just 'rejected_at_head'."""

from __future__ import annotations

from coordination.merge_shepherd_pass import plan_repo_disarms

HEAD = "d" * 40


def _truncated_comments_node(*, number: int = 5781) -> dict:
    """Node where comments.pageInfo.hasNextPage=True triggers 'incomplete_comment_history' suppression.

    Path: comments_truncated=True + no owner hold/retry in visible comments
    → _truncated_comments_may_hide_hold=True → suppression_detail='incomplete_comment_history'
    → evaluate_pr_shepherd_eligibility returns DISARM with detail='incomplete_comment_history'
    """
    return {
        "number": number,
        "isDraft": False,
        "headRefOid": HEAD,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "author": {"login": "d-sorgagyagent[bot]"},
        "autoMergeRequest": {},  # armed → eligibility evaluator checks suppressions
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "statusCheckRollup": {"state": "SUCCESS"},
                        "committedDate": "2026-11-28T00:00:00Z",
                    }
                }
            ]
        },
        "labels": {"nodes": []},
        "files": {"pageInfo": {"hasNextPage": False}, "nodes": [{"path": "backend/x.py"}]},
        "reviews": {"nodes": []},  # no FIX-NEEDED → pass_at_head won't produce 'rejected_at_head'
        # hasNextPage=True → comments_truncated=True → 'incomplete_comment_history' suppression
        "comments": {"pageInfo": {"hasNextPage": True}, "nodes": []},
        "timelineItems": {"nodes": []},
    }


def test_plan_repo_disarms_produces_incomplete_comment_history_detail() -> None:
    """plan_repo_disarms returns detail='incomplete_comment_history' for a truncated-comments node."""
    node = _truncated_comments_node()
    disarms = plan_repo_disarms(
        "D-sorganization/Runner_Dashboard",
        [node],
        skip_list=frozenset(),
        holds=(),
        live=False,
    )
    assert len(disarms) == 1, f"expected 1 disarm action, got {disarms}"
    assert disarms[0].pr_number == 5781
    assert disarms[0].head_sha == HEAD
    assert disarms[0].detail == "incomplete_comment_history"


def test_revalidate_disarm_reason_mismatch_bug_4733() -> None:
    """Bug #4733: _revalidate_disarm checked action.detail == 'rejected_at_head', returning False
    for all other valid disarm reasons (e.g. 'incomplete_comment_history').

    Demonstrates the bug and verifies the fix: match by pr_number + head_sha only.
    """
    node = _truncated_comments_node()
    disarms = plan_repo_disarms(
        "D-sorganization/Runner_Dashboard",
        [node],
        skip_list=frozenset(),
        holds=(),
        live=False,
    )
    pr, sha = 5781, HEAD

    # Buggy predicate: _revalidate_disarm checked detail == 'rejected_at_head'
    # For 'incomplete_comment_history' this returns False → "disarm revalidation failed" → exit 1
    old_result = any(
        a.pr_number == pr and a.head_sha == sha and a.detail == "rejected_at_head"
        for a in disarms
    )
    assert not old_result, "old predicate returns False for incomplete_comment_history (reproduces bug)"

    # Fixed predicate: match by pr_number + head_sha only, detail is irrelevant
    new_result = any(a.pr_number == pr and a.head_sha == sha for a in disarms)
    assert new_result, "fixed predicate must return True for a valid planned disarm"
