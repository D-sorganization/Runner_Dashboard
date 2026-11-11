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
    assert kind == "docs_tests_only"
    assert detail == "docs_tests_only"


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

    kind2, _ = shepherd_arm_eligibility(
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
