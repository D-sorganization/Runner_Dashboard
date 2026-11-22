"""#4618: scheduled merge shepherd executes DISARM during queue backoff."""

from __future__ import annotations

from coordination.merge_shepherd import ShepherdAction
from coordination.merge_shepherd_exact_head import collect_review_passes_from_github_reviews
from coordination.merge_shepherd_pass import plan_repo_disarms, plan_repo_pass

HEAD = "c" * 40


def _disarm_node(*, number: int = 99, queued: bool = True) -> dict:
    approve_review = {
        "state": "APPROVED",
        "commit": {"oid": HEAD},
        "author": {"login": "d-sorgclaudeagent[bot]"},
        "submittedAt": "2026-11-21T17:10:00Z",
    }
    late_fix_needed = {
        "state": "COMMENTED",
        "body": f"### Independent Exact-Head Acceptance Review - Verdict: FIX-NEEDED @{HEAD}",
        "commit": {"oid": HEAD},
        "author": {"login": "d-sorgcodexagent[bot]"},
        "submittedAt": "2026-11-21T17:29:05Z",
    }
    node = {
        "number": number,
        "isDraft": False,
        "headRefOid": HEAD,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "author": {"login": "d-sorgagyagent[bot]"},
        "autoMergeRequest": {} if queued else None,
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
        "labels": {"nodes": []},
        "files": {"pageInfo": {"hasNextPage": False}, "nodes": [{"path": "backend/x.py"}]},
        "reviews": {"nodes": [approve_review, late_fix_needed]},
        "comments": {"nodes": []},
    }
    return node


def test_plan_repo_disarms_during_queue_backoff() -> None:
    node = _disarm_node()
    arms, skips, backoff = plan_repo_pass(
        "D-sorganization/Runner_Dashboard",
        [node],
        skip_list=frozenset(),
        holds=(),
        armed_count=4,
        queue_depth=3,
        live=False,
    )
    assert backoff is True
    assert arms == []
    disarms = plan_repo_disarms(
        "D-sorganization/Runner_Dashboard",
        [node],
        skip_list=frozenset(),
        holds=(),
        live=False,
    )
    assert len(disarms) == 1
    assert disarms[0].kind == "disarm"
    assert disarms[0].pr_number == 99
    assert disarms[0].detail == "rejected_at_head"


def test_quoted_fix_needed_in_commented_review_is_ignored() -> None:
    review = {
        "state": "COMMENTED",
        "body": 'Discussing whether the label "FIX-NEEDED" applies here (no sha bound).',
        "commit": {"oid": HEAD},
        "author": {"login": "d-sorgcodexagent[bot]"},
        "submittedAt": "2026-11-21T17:29:05Z",
    }
    rows = collect_review_passes_from_github_reviews([review], 42, author_login="d-sorgagyagent[bot]")
    assert rows == ()


def test_quoted_fix_needed_with_matching_sha_in_commented_review_is_ignored() -> None:
    review_blockquote = {
        "state": "COMMENTED",
        "body": (
            "### Independent Exact-Head Acceptance Review\n"
            f"> FIX-NEEDED @{HEAD}\n"
            "The above quote should not trigger disarm."
        ),
        "commit": {"oid": HEAD},
        "author": {"login": "d-sorgcodexagent[bot]"},
        "submittedAt": "2026-11-21T17:29:05Z",
    }
    rows = collect_review_passes_from_github_reviews([review_blockquote], 42, author_login="d-sorgagyagent[bot]")
    assert rows == ()

    review_inline_quote = {
        "state": "COMMENTED",
        "body": f'### Independent Exact-Head Acceptance Review\nEarlier verdict was "FIX-NEEDED @{HEAD}".',
        "commit": {"oid": HEAD},
        "author": {"login": "d-sorgcodexagent[bot]"},
        "submittedAt": "2026-11-21T17:29:05Z",
    }
    rows2 = collect_review_passes_from_github_reviews([review_inline_quote], 42, author_login="d-sorgagyagent[bot]")
    assert rows2 == ()

    review_backticks = {
        "state": "COMMENTED",
        "body": f"### Independent Exact-Head Acceptance Review\nReferencing `FIX-NEEDED @{HEAD}`.",
        "commit": {"oid": HEAD},
        "author": {"login": "d-sorgcodexagent[bot]"},
        "submittedAt": "2026-11-21T17:29:05Z",
    }
    rows3 = collect_review_passes_from_github_reviews([review_backticks], 42, author_login="d-sorgagyagent[bot]")
    assert rows3 == ()


def test_genuine_fix_needed_still_disarms() -> None:
    node = _disarm_node(number=42, queued=True)
    passes = node["reviews"]["nodes"]
    from coordination.merge_shepherd import PRCandidate, evaluate_pr_shepherd_eligibility
    from coordination.merge_shepherd_exact_head import collect_review_passes_from_github_reviews

    github_passes = collect_review_passes_from_github_reviews(
        passes, 42, author_login="d-sorgagyagent[bot]"
    )
    candidate = PRCandidate(
        repo="D-sorganization/Runner_Dashboard",
        pr_number=42,
        head_sha=HEAD,
        auto_merge_enabled=True,
        is_queued=True,
        checks_status="SUCCESS",
        review_passes=github_passes,
    )
    decision = evaluate_pr_shepherd_eligibility(candidate)
    assert decision.action == ShepherdAction.DISARM
