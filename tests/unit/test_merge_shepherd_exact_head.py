"""Exact-head PASS eligibility for merge shepherd (#4249 slice)."""

from __future__ import annotations

from datetime import UTC, datetime

from coordination.merge_shepherd_exact_head import (
    DEFAULT_REVIEW_PASS_TTL_HOURS,
    HeadReviewPass,
    head_sha_matches,
    pass_at_head,
    shepherd_arm_eligibility,
)


def _pass(pr: int, sha: str, *, recorded_at: str = "2026-11-10T12:00:00+00:00") -> HeadReviewPass:
    return HeadReviewPass(
        pr=pr,
        head_sha=sha,
        verdict="approve",
        recorded_at=recorded_at,
        reviewer_model="gpt-6-luna",
    )


def test_head_sha_matches_accepts_short_prefix() -> None:
    assert head_sha_matches("0000ab01", "0000ab0100000000000000000000000000000000")


def test_pass_at_head_rejects_wrong_sha() -> None:
    ok, detail = pass_at_head(
        [_pass(1, "aaa1111")], pr=1, head_sha="bbb2222", now=datetime(2026, 11, 10, 13, tzinfo=UTC)
    )
    assert not ok and detail == "missing_pass_at_head"


def test_pass_at_head_rejects_stale_pass() -> None:
    ok, detail = pass_at_head(
        [_pass(1, "abc1234", recorded_at="2020-01-01T00:00:00+00:00")],
        pr=1,
        head_sha="abc1234",
        now=datetime(2026, 11, 10, 13, tzinfo=UTC),
        ttl_hours=DEFAULT_REVIEW_PASS_TTL_HOURS,
    )
    assert not ok and detail == "stale_unverified"


def test_shepherd_arm_eligibility_docs_only_without_hub_pass() -> None:
    kind, detail = shepherd_arm_eligibility(
        review_decision="REVIEW_REQUIRED",
        file_paths=["docs/a.md"],
        additions=1,
        deletions=0,
        line_cap=300,
        labels=(),
        checks_state="SUCCESS",
        files_complete=True,
        pr_number=9,
        head_sha="00ff00ff",
        review_passes=(),
    )
    assert kind == "no_approval"
    assert detail == "missing_pass_at_head"


def test_shepherd_arm_eligibility_reviewed_requires_pass_at_head() -> None:
    kind, detail = shepherd_arm_eligibility(
        review_decision="APPROVED",
        file_paths=["backend/x.py"],
        additions=10,
        deletions=2,
        line_cap=300,
        labels=(),
        checks_state="SUCCESS",
        files_complete=True,
        pr_number=42,
        head_sha="sha42full",
        review_passes=(),
    )
    assert kind == "no_approval"
    assert detail == "missing_pass_at_head"

    kind2, detail2 = shepherd_arm_eligibility(
        review_decision="APPROVED",
        file_paths=["backend/x.py"],
        additions=10,
        deletions=2,
        line_cap=300,
        labels=(),
        checks_state="SUCCESS",
        files_complete=True,
        pr_number=42,
        head_sha="sha42full",
        review_passes=[_pass(42, "sha42fu")],
        now=datetime(2026, 11, 10, 13, tzinfo=UTC),
    )
    assert kind2 == "reviewed"
    assert detail2 == "review_decision_approved+exact_head_pass"


def test_shepherd_arm_eligibility_accepts_exact_head_pass_without_formal_review_decision() -> None:
    # Acceptance #5 regression: empty reviewDecision with verified exact-head verdict arms (#4544)
    kind, detail = shepherd_arm_eligibility(
        review_decision="",
        file_paths=["backend/x.py"],
        additions=50,
        deletions=10,
        line_cap=300,
        labels=(),
        checks_state="SUCCESS",
        files_complete=True,
        pr_number=100,
        head_sha="a1b2c3d4e5f6",  # pragma: allowlist secret
        review_passes=[_pass(100, "a1b2c3d4")],  # pragma: allowlist secret
        now=datetime(2026, 11, 10, 13, tzinfo=UTC),
    )
    assert kind == "reviewed"
    assert detail == "exact_head_verdict+exact_head_pass"


def test_collect_review_passes_from_comments() -> None:
    from coordination.merge_shepherd_exact_head import collect_review_passes_from_comments

    comments = [
        {"body": "Looking good", "createdAt": "2026-11-10T12:00:00Z", "author": {"login": "bot"}},
        {
            "body": "STAFF_RESULT: review approve #55 @000011112222\nLGTM",  # pragma: allowlist secret
            "createdAt": "2026-11-10T12:05:00Z",
            "author": {"login": "claude-bot"},
        },
    ]
    passes = collect_review_passes_from_comments(comments, pr=55)
    assert len(passes) == 1
    assert passes[0].pr == 55
    assert passes[0].head_sha == "000011112222"
    assert passes[0].verdict == "approve"
    assert passes[0].reviewer_model == "claude-bot"


def test_collect_review_passes_from_github_reviews() -> None:
    from coordination.merge_shepherd_exact_head import collect_review_passes_from_github_reviews

    reviews = [
        {
            "state": "APPROVED",
            "submittedAt": "2026-11-10T12:10:00Z",
            "author": {"login": "codex-bot"},
            "commit": {"oid": "000033334444"},  # pragma: allowlist secret
        },
        {
            "state": "COMMENTED",
            "submittedAt": "2026-11-10T12:08:00Z",
            "author": {"login": "other"},
            "commit": {"oid": "000055556666"},
        },
    ]
    passes = collect_review_passes_from_github_reviews(reviews, pr=77)
    assert len(passes) == 1
    assert passes[0].pr == 77
    assert passes[0].head_sha == "000033334444"
    assert passes[0].verdict == "approve"


def test_collect_review_passes_from_store_accepts_acceptance_review_roles() -> None:
    from types import SimpleNamespace

    from coordination.merge_shepherd_exact_head import collect_review_passes_from_store

    runs = [
        SimpleNamespace(
            repo="Runner_Dashboard",
            role="acceptance-review:claude",
            status="succeeded",
            outcome="STAFF_RESULT: review approve #88 @000077778888",  # pragma: allowlist secret
            ended_at="2026-11-10T12:15:00Z",
            model="claude-3-5-sonnet",
        ),
        SimpleNamespace(
            repo="Runner_Dashboard",
            role="acceptance-review:codex",
            status="succeeded",
            outcome="STAFF_RESULT: review approve #89 @000099990000",  # pragma: allowlist secret
            ended_at="2026-11-10T12:20:00Z",
            model="gpt-6.1-sol",
        ),
    ]
    store = SimpleNamespace(list_runs=lambda limit=500: runs)
    passes = collect_review_passes_from_store(store, "Runner_Dashboard")
    assert len(passes) == 2
    prs = {p.pr for p in passes}
    assert prs == {88, 89}
