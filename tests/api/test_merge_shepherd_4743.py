"""TDD tests for RD #4743 — three merge-shepherd failure modes.

Fix 1: Queued PRs that fail an eligibility guard return SKIP instead of DISARM.
Fix 2: OPEN_PRS_QUERY has timelineItems(last: 100) × 100 PRs → node count near limit.
Fix 3: d-sorggeneralagent[bot] reviews unrecognized (_login_provider returns "").
"""

from __future__ import annotations

import pytest

from coordination.merge_shepherd import (
    BOUNCE_LIMIT,
    IneligibleReason,
    PRCandidate,
    ShepherdAction,
    evaluate_pr_shepherd_eligibility,
)
from coordination.merge_shepherd_exact_head import collect_review_passes_from_comments
from coordination.merge_shepherd_pass import GRAPHQL_NODE_LIMIT, OPEN_PRS_QUERY, graphql_connection_node_count

# ---------------------------------------------------------------------------
# Fix 1: queued PRs with an eligibility failure must return DISARM, not SKIP
# ---------------------------------------------------------------------------

REPO = "Runner_Dashboard"
_BASE = dict(head_sha="a" * 40, checks_status="SUCCESS", files_complete=True, file_paths=("x.py",))


def _queued(**extra: object) -> PRCandidate:
    return PRCandidate(pr_number=1, repo=REPO, is_queued=True, **{**_BASE, **extra})  # type: ignore[arg-type]


def test_queued_draft_returns_disarm() -> None:
    """A queued PR that is now a draft must be dequeued (DISARM), not silently skipped."""
    d = evaluate_pr_shepherd_eligibility(_queued(is_draft=True))
    assert d.action is ShepherdAction.DISARM, f"expected DISARM, got {d.action} ({d.reason})"
    assert d.reason is IneligibleReason.DRAFT


def test_queued_conflict_returns_disarm() -> None:
    d = evaluate_pr_shepherd_eligibility(_queued(mergeable="CONFLICTING"))
    assert d.action is ShepherdAction.DISARM, f"expected DISARM, got {d.action} ({d.reason})"
    assert d.reason is IneligibleReason.CONFLICT


def test_queued_held_returns_disarm() -> None:
    d = evaluate_pr_shepherd_eligibility(_queued(labels=("merge:hold",), title="fix: something"))
    assert d.action is ShepherdAction.DISARM, f"expected DISARM, got {d.action} ({d.reason})"
    assert d.reason is IneligibleReason.HELD


def test_queued_bounce_limit_returns_disarm() -> None:
    d = evaluate_pr_shepherd_eligibility(_queued(bounces=BOUNCE_LIMIT))
    assert d.action is ShepherdAction.DISARM, f"expected DISARM, got {d.action} ({d.reason})"
    assert d.reason is IneligibleReason.BOUNCE_LIMIT


def test_queued_retry_suppressed_returns_disarm() -> None:
    d = evaluate_pr_shepherd_eligibility(_queued(retry_suppressed=True, retry_suppressed_detail="flake"))
    assert d.action is ShepherdAction.DISARM, f"expected DISARM, got {d.action} ({d.reason})"
    assert d.reason is IneligibleReason.RETRY_SUPPRESSED


def test_queued_drained_returns_disarm() -> None:
    d = evaluate_pr_shepherd_eligibility(_queued(is_drained=True, is_exempt=False))
    assert d.action is ShepherdAction.DISARM, f"expected DISARM, got {d.action} ({d.reason})"
    assert d.reason is IneligibleReason.DRAINED


def test_queued_already_queued_healthy_returns_skip() -> None:
    """A queued PR that passes all structural guards must SKIP — it stays in the merge queue."""
    d = evaluate_pr_shepherd_eligibility(_queued())
    assert d.action is ShepherdAction.SKIP, f"expected SKIP, got {d.action} ({d.reason})"
    assert d.reason is IneligibleReason.ALREADY_QUEUED


def test_not_queued_draft_still_skips() -> None:
    """Non-queued draft should remain SKIP — DISARM is only for queued PRs."""
    c = PRCandidate(pr_number=2, repo=REPO, is_queued=False, is_draft=True, **_BASE)  # type: ignore[arg-type]
    d = evaluate_pr_shepherd_eligibility(c)
    assert d.action is ShepherdAction.SKIP
    assert d.reason is IneligibleReason.DRAFT


# ---------------------------------------------------------------------------
# Fix 2: OPEN_PRS_QUERY inner page sizes must be small enough to avoid 502/504
# ---------------------------------------------------------------------------

import re as _re


def test_open_prs_query_timeline_items_page_size() -> None:
    """timelineItems(last: N) in OPEN_PRS_QUERY must be <= 20 to avoid 502/504 from GitHub."""
    matches = _re.findall(r"timelineItems\([^)]*\blast:\s*(\d+)", OPEN_PRS_QUERY)
    assert matches, "OPEN_PRS_QUERY must contain a timelineItems connection"
    for n in matches:
        assert int(n) <= 20, (
            f"timelineItems(last: {n}) exceeds safe limit of 20 — "
            "100 PRs × {n} items inflates the response payload and triggers GitHub 502/504."
        )


def test_open_prs_query_files_page_size() -> None:
    """files(first: N) in OPEN_PRS_QUERY must be <= 50 to avoid 502/504 from GitHub."""
    matches = _re.findall(r"\bfiles\s*\([^)]*\bfirst:\s*(\d+)", OPEN_PRS_QUERY)
    assert matches, "OPEN_PRS_QUERY must contain a files connection"
    for n in matches:
        assert int(n) <= 50, (
            f"files(first: {n}) exceeds safe limit of 50 — "
            "100 PRs × {n} files inflates the response payload and triggers GitHub 502/504."
        )


def test_open_prs_query_node_count_within_limit() -> None:
    """After page-size reductions the computed node charge must stay under GRAPHQL_NODE_LIMIT."""
    count = graphql_connection_node_count(OPEN_PRS_QUERY)
    assert count <= GRAPHQL_NODE_LIMIT, (
        f"OPEN_PRS_QUERY charges {count:,} nodes (limit {GRAPHQL_NODE_LIMIT:,}). Reduce inner page sizes."
    )


# ---------------------------------------------------------------------------
# Fix 3: d-sorggeneralagent[bot] reviews must be accepted as independent
# ---------------------------------------------------------------------------

_SHA = "b" * 40
_TS = "2026-11-28T10:00:00Z"


def _comment(reviewer_login: str, verdict: str = "PASS") -> dict:
    return {
        "author": {"login": reviewer_login},
        "body": f"STAFF_RESULT: review approve #7 @{_SHA}",
        "createdAt": _TS,
    }


def test_general_agent_review_accepted_when_independent() -> None:
    """d-sorggeneralagent[bot] reviewing a d-sorgcodexagent[bot]-authored PR must count."""
    passes = collect_review_passes_from_comments(
        [_comment("d-sorggeneralagent[bot]")],
        pr=7,
        author_login="d-sorgcodexagent[bot]",
    )
    assert len(passes) >= 1, (
        "d-sorggeneralagent[bot] review was dropped — _login_provider must resolve its provider family"
    )
    assert passes[0].verdict == "approve"


def test_general_agent_review_rejected_when_same_family() -> None:
    """d-sorggeneralagent[bot] reviewing a d-sorggeneralagent[bot]-authored PR must be rejected."""
    passes = collect_review_passes_from_comments(
        [_comment("d-sorggeneralagent[bot]")],
        pr=7,
        author_login="d-sorggeneralagent[bot]",
    )
    assert len(passes) == 0, "Same-family review must still be filtered out"
