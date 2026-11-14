"""Formal GitHub APPROVE posting for hub code-reviewer verdicts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from staff.independent_exact_head_verdict import VerdictPostError
from staff.staff_github_pr_review import (
    ensure_runner_dashboard_formal_approve,
    fetch_pull_reviews,
    own_stale_changes_requested_review_ids,
    submit_pull_request_review,
)

from shared_scripts.independent_exact_head_review import RUNNER_DASHBOARD_BOT_LOGIN

pytestmark = pytest.mark.unit

_FAKE_GHS = "ghs_" + "A" * 40 + "_jwtbody"
_HEAD = "cccccccccccccccccccccccccccccccccccccccc"
_OLD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_fetch_pull_reviews_follows_github_link_pagination() -> None:
    """Stale bot CR on page 2 must be visible (default GitHub page size is 30)."""
    filler = [{"id": i, "state": "COMMENTED", "commit_id": _OLD, "user": {"login": "other-bot"}} for i in range(30)]
    stale_cr = {
        "id": 9001,
        "state": "CHANGES_REQUESTED",
        "commit_id": _OLD,
        "user": {"login": RUNNER_DASHBOARD_BOT_LOGIN},
    }
    page1 = httpx.Response(
        200,
        json=filler,
        headers={
            "link": (
                "<https://api.github.com/repos/D-sorganization/Runner_Dashboard/pulls/1/reviews?page=2&per_page=100>; "
                'rel="next"'
            )
        },
        request=httpx.Request("GET", "https://api.github.com/page1"),
    )
    page2 = httpx.Response(
        200,
        json=[stale_cr],
        headers={},
        request=httpx.Request("GET", "https://api.github.com/page2"),
    )

    def _get(url: str, **kwargs: object) -> httpx.Response:
        if "page=2" in url:
            return page2
        return page1

    with patch("staff.staff_github_pr_review.httpx.get", side_effect=_get):
        reviews = fetch_pull_reviews(
            full_repo="D-sorganization/Runner_Dashboard",
            pr_number=1,
            token=_FAKE_GHS,
        )
    assert len(reviews) == 31
    assert own_stale_changes_requested_review_ids(reviews, head_sha=_HEAD) == [9001]


def test_submit_pull_request_review_posts_approve_event() -> None:
    response = httpx.Response(200, request=httpx.Request("POST", "https://api.github.com"))
    with patch("staff.staff_github_pr_review.httpx.post", return_value=response) as post:
        submit_pull_request_review(
            full_repo="D-sorganization/Runner_Dashboard",
            pr_number=9,
            commit_id=_HEAD,
            event="APPROVE",
            body="ok",
            token=_FAKE_GHS,
        )
    assert post.call_args.kwargs["json"]["event"] == "APPROVE"
    assert post.call_args.kwargs["json"]["commit_id"] == _HEAD


def test_ensure_formal_approve_dismisses_stale_cr_then_submits() -> None:
    reviews = [
        {"id": 90, "state": "CHANGES_REQUESTED", "commit_id": _OLD, "user": {"login": RUNNER_DASHBOARD_BOT_LOGIN}},
    ]
    fake = MagicMock(token=_FAKE_GHS, expires_at=1_700_000_000.0)
    with patch("staff.staff_github_pr_review.mint_runner_dashboard_verdict_token", return_value=fake):
        with patch("staff.staff_github_pr_review.fetch_pull_reviews", return_value=reviews):
            with patch("staff.staff_github_pr_review.dismiss_pull_review") as dismiss:
                with patch("staff.staff_github_pr_review.submit_pull_request_review") as submit:
                    ensure_runner_dashboard_formal_approve(
                        repo="Runner_Dashboard",
                        pr_number=42,
                        head_sha=_HEAD,
                        body="PASS",
                    )
    dismiss.assert_called_once()
    assert dismiss.call_args.kwargs["review_id"] == 90
    submit.assert_called_once()
    assert submit.call_args.kwargs["event"] == "APPROVE"


def test_ensure_formal_approve_skips_when_already_approved_at_head() -> None:
    reviews = [
        {"id": 1, "state": "APPROVED", "commit_id": _HEAD, "user": {"login": RUNNER_DASHBOARD_BOT_LOGIN}},
    ]
    fake = MagicMock(token=_FAKE_GHS, expires_at=1_700_000_000.0)
    with patch("staff.staff_github_pr_review.mint_runner_dashboard_verdict_token", return_value=fake):
        with patch("staff.staff_github_pr_review.fetch_pull_reviews", return_value=reviews):
            with patch("staff.staff_github_pr_review.submit_pull_request_review") as submit:
                ensure_runner_dashboard_formal_approve(
                    repo="Runner_Dashboard",
                    pr_number=42,
                    head_sha=_HEAD,
                    body="PASS",
                )
    submit.assert_not_called()


def test_publish_pass_triggers_formal_approve() -> None:
    from staff.independent_exact_head_verdict import publish_independent_exact_head_verdict

    fake = MagicMock(token=_FAKE_GHS, expires_at=1_700_000_000.0)
    with patch("staff.independent_exact_head_verdict.github_app_auth_configured", return_value=True):
        with patch("staff.independent_exact_head_verdict.mint_installation_access_token", return_value=fake):
            with patch("staff.independent_exact_head_verdict.post_pr_comment"):
                with patch("staff.staff_github_pr_review.ensure_runner_dashboard_formal_approve") as formal:
                    publish_independent_exact_head_verdict(
                        repo="Runner_Dashboard",
                        pr_number=9,
                        verdict="PASS",
                        head_sha=_HEAD,
                        reviewer_model="gpt-5.x-codex",
                    )
    formal.assert_called_once()
    assert formal.call_args.kwargs["head_sha"] == _HEAD


def test_finalize_approve_posts_formal_review() -> None:
    from staff.review import finalize_code_reviewer_run

    with patch("staff.independent_exact_head_verdict.fetch_pull_head_sha", return_value=_HEAD):
        with patch("staff.staff_github_pr_review.ensure_runner_dashboard_formal_approve") as formal:
            out = finalize_code_reviewer_run(
                status="succeeded",
                failure_class="",
                retryable=False,
                error="",
                remediation="",
                result_line="STAFF_RESULT: review approve #42",
                same_provider=False,
                repo="Runner_Dashboard",
                reviewer_model="codex",
                expected_pr_number=42,
            )
    assert out.status == "succeeded"
    formal.assert_called_once()


def test_finalize_approve_fails_loud_on_formal_post_error() -> None:
    from staff.review import finalize_code_reviewer_run

    with patch("staff.independent_exact_head_verdict.fetch_pull_head_sha", return_value=_HEAD):
        with patch(
            "staff.staff_github_pr_review.ensure_runner_dashboard_formal_approve",
            side_effect=VerdictPostError("nope", http_status=403),
        ):
            out = finalize_code_reviewer_run(
                status="succeeded",
                failure_class="",
                retryable=False,
                error="",
                remediation="",
                result_line="STAFF_RESULT: review approve #42",
                same_provider=False,
                repo="Runner_Dashboard",
                reviewer_model="codex",
                expected_pr_number=42,
            )
    assert out.status == "failed"
    assert out.failure_class == "provider_error"
    assert "403" in out.remediation
