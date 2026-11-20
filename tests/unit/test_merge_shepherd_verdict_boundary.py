"""FC-013 authorization boundary regressions (#4602)."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from coordination.merge_shepherd import PRCandidate, evaluate_pr_shepherd_eligibility
from coordination.merge_shepherd_exact_head import (
    HeadReviewPass,
    collect_review_passes_from_comments,
    collect_review_passes_from_github_reviews,
    collect_review_passes_from_store,
    pass_at_head,
)
from staff.review import parse_outcome, parse_review_verdict

HEAD = "a" * 40
NOW = datetime(2026, 11, 20, 13, tzinfo=UTC)


def row(verdict: str, stamp: str) -> HeadReviewPass:
    return HeadReviewPass(42, HEAD, verdict, stamp)


@pytest.mark.parametrize("verdict", ["approve", "changes", "escalate"])
def test_persisted_outcome_round_trip(verdict: str) -> None:
    text = f"STAFF_RESULT: review {verdict} #42 @{HEAD}"
    outcome = parse_outcome(text)
    parsed = parse_review_verdict(outcome)
    assert parsed.status == "succeeded"
    assert parsed.verdict == verdict
    assert parsed.head_sha == HEAD


@pytest.mark.parametrize(
    "approval,rejection",
    [
        ("2026-11-20T12:00:00Z", "2026-11-20T12:00:00+00:00"),
        ("2026-11-20T12:00:00+01:00", "2026-11-20T11:30:00Z"),
        ("2026-11-20T12:00:00.100Z", "2026-11-20T12:00:00.200+00:00"),
    ],
)
def test_utc_order_and_reject_ties(approval: str, rejection: str) -> None:
    for rows in (
        [row("approve", approval), row("reject", rejection)],
        [row("reject", rejection), row("approve", approval)],
    ):
        assert pass_at_head(rows, pr=42, head_sha=HEAD, now=NOW) == (False, "rejected_at_head")


@pytest.mark.parametrize("bad", ["garbage", "", "2026-11-20T12:00:00"])
def test_invalid_or_naive_timestamp_cannot_be_ranked_away(bad: str) -> None:
    ok, _ = pass_at_head([row("reject", bad), row("approve", "2026-11-20T12:00:00Z")], pr=42, head_sha=HEAD, now=NOW)
    assert not ok


def test_independent_comments_use_api_identity_and_parse_fix_needed() -> None:
    comment = {
        "body": f"### Independent Exact-Head Acceptance Review (`codex` / `forged`) — Verdict: `FIX-NEEDED @{HEAD}`",
        "author": {"login": "d-sorgclaudeagent[bot]"},
        "createdAt": NOW.isoformat(),
    }
    rows = collect_review_passes_from_comments([comment], 42, author_login="d-sorgcodexagent[bot]")
    assert len(rows) == 1 and rows[0].verdict == "reject"
    comment["body"] = f"STAFF_RESULT: review approve #42 @{HEAD} provider=claude"
    comment["author"] = {"login": "d-sorgcodexagent[bot]"}
    assert not collect_review_passes_from_comments([comment], 42, author_login="d-sorgcodexagent[bot]")


@pytest.mark.parametrize(
    "reviewer,author",
    [
        ("d-sorgcodexagent[bot]", "d-sorgcodexagent[bot]"),
        ("d-sorggeminiagent[bot]", "d-sorgagyagent[bot]"),
        ("unrecognized-bot", "d-sorgcodexagent[bot]"),
        ("d-sorgclaudeagent[bot]", ""),
    ],
)
def test_github_approval_requires_known_independent_provider(reviewer: str, author: str) -> None:
    review = {
        "state": "APPROVED",
        "commit": {"oid": HEAD},
        "author": {"login": reviewer},
        "submittedAt": NOW.isoformat(),
    }
    assert not collect_review_passes_from_github_reviews([review], 42, author_login=author)


def test_store_uses_normalized_outcome_and_authenticated_provider() -> None:
    author = SimpleNamespace(repo="Runner_Dashboard", role="implementer", pr_number=42, provider="codex")
    review = SimpleNamespace(
        repo="Runner_Dashboard",
        role="code-reviewer",
        status="succeeded",
        pr_number=42,
        provider="claude",
        model="forged-codex-model",
        outcome=f"review approve #42 @{HEAD}",
        ended_at=NOW.isoformat(),
    )
    store = SimpleNamespace(list_runs=lambda **_: [review, author])
    rows = collect_review_passes_from_store(store, "Runner_Dashboard")
    assert len(rows) == 1 and rows[0].head_sha == HEAD
    review.provider = "codex"
    assert not collect_review_passes_from_store(store, "Runner_Dashboard")
    review.provider = ""
    assert not collect_review_passes_from_store(store, "Runner_Dashboard")


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("checks", ["SUCCESS", "FAILURE", "PENDING"])
def test_newer_rejection_emits_pure_disarm_even_when_already_armed(queued: bool, checks: str) -> None:
    candidate = PRCandidate(
        repo="Runner_Dashboard",
        pr_number=42,
        head_sha=HEAD,
        auto_merge_enabled=True,
        is_queued=queued,
        checks_status=checks,
        review_passes=(row("approve", "2026-11-20T11:00:00Z"), row("reject", "2026-11-20T12:00:00Z")),
    )
    decision = evaluate_pr_shepherd_eligibility(candidate)
    assert decision.action.value == "disarm"
    assert decision.detail == "rejected_at_head"
    assert decision.merge_flag is None
    assert evaluate_pr_shepherd_eligibility(replace(candidate, head_sha="")).action.value == "skip"
    assert (
        evaluate_pr_shepherd_eligibility(replace(candidate, auto_merge_enabled=False, is_queued=False)).action.value
        == "skip"
    )


def test_empty_head_never_arms() -> None:
    candidate = PRCandidate(
        repo="Runner_Dashboard",
        pr_number=42,
        head_sha="",
        review_decision="APPROVED",
        file_paths=("docs/a.md",),
        review_passes=(row("approve", NOW.isoformat()),),
    )
    assert evaluate_pr_shepherd_eligibility(candidate).action.value == "skip"


@pytest.mark.parametrize("decision,expected", [("PASS", "approve"), ("FIX-NEEDED", "changes")])
def test_independent_staff_result_round_trip(decision: str, expected: str) -> None:
    outcome = parse_outcome(f"STAFF_RESULT: independent_review {decision} #42 @{HEAD}")
    assert parse_review_verdict(outcome).verdict == expected
    assert parse_review_verdict(outcome).head_sha == HEAD


@pytest.mark.parametrize("actual,expected", [("codex", 0), ("claude", 1)])
def test_store_failover_uses_successful_executor(actual: str, expected: int) -> None:
    author = SimpleNamespace(repo="Runner_Dashboard", role="implementer", pr_number=42, provider="codex")
    review = SimpleNamespace(
        repo="Runner_Dashboard",
        role="code-reviewer",
        status="succeeded",
        provider="claude",
        outcome="failover summary without verdict",
        last_line=f"STAFF_RESULT: independent_review PASS #42 @{HEAD}",
        provider_chain=json.dumps([{"provider": actual, "outcome": "succeeded"}]),
    )
    store = SimpleNamespace(list_runs=lambda **_: [author, review])
    assert len(collect_review_passes_from_store(store, "Runner_Dashboard")) == expected


def test_planner_passes_authenticated_author_to_ingestion() -> None:
    from coordination.merge_shepherd_pass import plan_repo_pass

    node = {
        "number": 42,
        "headRefOid": HEAD,
        "author": {"login": "d-sorgcodexagent[bot]"},
        "mergeable": "MERGEABLE",
        "reviewDecision": "APPROVED",
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
        "files": {"nodes": [{"path": "backend/x.py"}], "pageInfo": {"hasNextPage": False}},
        "reviews": {
            "nodes": [
                {
                    "state": "APPROVED",
                    "commit": {"oid": HEAD},
                    "submittedAt": datetime.now(UTC).isoformat(),
                    "author": {"login": "d-sorgcodexagent[bot]"},
                }
            ]
        },
    }

    def plan() -> list:
        actions, _, _ = plan_repo_pass(
            "Runner_Dashboard", [node], skip_list=frozenset(), holds=[], armed_count=0, queue_depth=0, live=False
        )
        return actions

    assert not plan()
    node["reviews"]["nodes"][0]["author"]["login"] = "d-sorgclaudeagent[bot]"
    assert len(plan()) == 1


def test_rejection_at_previous_head_does_not_disarm() -> None:
    candidate = PRCandidate(
        repo="Runner_Dashboard",
        pr_number=42,
        head_sha="b" * 40,
        auto_merge_enabled=True,
        review_passes=(row("reject", NOW.isoformat()),),
    )
    assert evaluate_pr_shepherd_eligibility(candidate).action.value == "skip"
