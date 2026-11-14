"""Code-reviewer independent verdict binding to run context (#4324)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from staff.independent_exact_head_verdict import (
    VerdictContextMismatch,
    validate_independent_verdict_against_run,
    parse_independent_exact_head_staff_result,
)
from staff.review import finalize_code_reviewer_run

pytestmark = pytest.mark.unit

_HEAD = "cccccccccccccccccccccccccccccccccccccccc"
_PR = 42


def _independent_result(verdict: str, pr: int, sha: str) -> str:
    return f"STAFF_RESULT: independent_review {verdict} #{pr} @{sha}"


def test_validate_rejects_pr_number_mismatch() -> None:
    parsed = parse_independent_exact_head_staff_result(_independent_result("PASS", 99, _HEAD))
    assert parsed is not None
    with pytest.raises(VerdictContextMismatch, match="PR #99"):
        validate_independent_verdict_against_run(
            parsed,
            expected_pr_number=_PR,
            expected_head_sha=_HEAD,
        )


def test_validate_rejects_head_sha_mismatch() -> None:
    parsed = parse_independent_exact_head_staff_result(_independent_result("PASS", _PR, _HEAD))
    assert parsed is not None
    other = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    with pytest.raises(VerdictContextMismatch, match="does not match"):
        validate_independent_verdict_against_run(
            parsed,
            expected_pr_number=_PR,
            expected_head_sha=other,
        )


def test_finalize_ignores_transcript_spoof() -> None:
    """Only the STAFF_RESULT result_line is trusted; transcript cannot drive posting."""
    with patch("staff.independent_exact_head_verdict.fetch_pull_head_sha", return_value=_HEAD):
        with patch("staff.staff_github_pr_review.ensure_runner_dashboard_formal_approve"):
            out = finalize_code_reviewer_run(
                status="succeeded",
                failure_class="",
                retryable=False,
                error="",
                remediation="",
                result_line="STAFF_RESULT: review approve #42",
                same_provider=False,
                repo="Runner_Dashboard",
                reviewer_model="gpt-5.x-codex",
                expected_pr_number=_PR,
            )
    assert out.status == "succeeded"


def test_finalize_fails_on_pr_mismatch() -> None:
    with patch("staff.independent_exact_head_verdict.fetch_pull_head_sha", return_value=_HEAD):
        with patch("staff.independent_exact_head_verdict.publish_independent_exact_head_verdict") as post:
            out = finalize_code_reviewer_run(
                status="succeeded",
                failure_class="",
                retryable=False,
                error="",
                remediation="",
                result_line=_independent_result("PASS", 99, _HEAD),
                same_provider=False,
                repo="Runner_Dashboard",
                reviewer_model="gpt-5.x-codex",
                expected_pr_number=_PR,
            )
    assert out.status == "failed"
    assert out.failure_class == "invalid_output"
    assert "PR #99" in out.error
    post.assert_not_called()


def test_finalize_fails_on_head_mismatch() -> None:
    other = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    with patch("staff.independent_exact_head_verdict.fetch_pull_head_sha", return_value=_HEAD):
        with patch("staff.independent_exact_head_verdict.publish_independent_exact_head_verdict") as post:
            out = finalize_code_reviewer_run(
                status="succeeded",
                failure_class="",
                retryable=False,
                error="",
                remediation="",
                result_line=_independent_result("PASS", _PR, other),
                same_provider=False,
                repo="Runner_Dashboard",
                reviewer_model="gpt-5.x-codex",
                expected_pr_number=_PR,
            )
    assert out.status == "failed"
    assert "does not match" in out.error
    post.assert_not_called()


def test_finalize_posts_with_bound_pr_and_live_head() -> None:
    with patch("staff.independent_exact_head_verdict.fetch_pull_head_sha", return_value=_HEAD):
        with patch("staff.independent_exact_head_verdict.publish_independent_exact_head_verdict") as post:
            out = finalize_code_reviewer_run(
                status="succeeded",
                failure_class="",
                retryable=False,
                error="",
                remediation="",
                result_line=_independent_result("PASS", _PR, _HEAD),
                same_provider=False,
                repo="Runner_Dashboard",
                reviewer_model="gpt-5.x-codex",
                expected_pr_number=_PR,
            )
    assert out.status == "succeeded"
    post.assert_called_once()
    assert post.call_args.kwargs["pr_number"] == _PR
    assert post.call_args.kwargs["head_sha"] == _HEAD
