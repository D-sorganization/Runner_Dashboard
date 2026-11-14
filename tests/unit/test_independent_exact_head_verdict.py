"""Tests for hub exact-head verdict posting (#4323)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from staff.independent_exact_head_verdict import (
    VerdictPostError,
    mint_runner_dashboard_verdict_token,
    parse_independent_exact_head_staff_result,
    post_pr_comment,
    publish_independent_exact_head_verdict,
)

pytestmark = pytest.mark.unit

_FAKE_GHS = "ghs_" + "B" * 40 + "_jwtbody"


def test_parse_independent_exact_head_staff_result() -> None:
    text = "done\nSTAFF_RESULT: independent_review PASS #42 @aabbccddeeff\n"
    parsed = parse_independent_exact_head_staff_result(text)
    assert parsed is not None
    assert parsed.verdict == "PASS"
    assert parsed.pr_number == 42
    assert parsed.head_sha == "aabbccddeeff"


def test_mint_runner_dashboard_verdict_token_uses_hub_app_path() -> None:
    fake = MagicMock(token=_FAKE_GHS, expires_at=1_700_000_000.0)
    with patch("staff.independent_exact_head_verdict.github_app_auth_configured", return_value=True):
        with patch("staff.independent_exact_head_verdict.mint_installation_access_token", return_value=fake) as mint:
            cred = mint_runner_dashboard_verdict_token("Runner_Dashboard")
    assert cred.token == _FAKE_GHS
    assert cred.source == "github_app"
    mint.assert_called_once()
    assert mint.call_args.kwargs["repositories"] == ["Runner_Dashboard"]
    perms = mint.call_args.kwargs["permissions"]
    assert perms["issues"] == "write"
    assert perms["pull_requests"] == "write"


def test_post_pr_comment_403_raises_verdict_post_error() -> None:
    request = httpx.Request("POST", "https://api.github.com/repos/D-sorganization/X/issues/1/comments")
    response = httpx.Response(403, request=request, text='{"message":"Resource not accessible by integration"}')
    with patch("staff.independent_exact_head_verdict.httpx.post", return_value=response):
        with pytest.raises(VerdictPostError) as exc:
            post_pr_comment(
                full_repo="D-sorganization/X",
                pr_number=1,
                body="test",
                token=_FAKE_GHS,
            )
    assert exc.value.http_status == 403


def test_publish_independent_exact_head_verdict_posts_with_minted_token() -> None:
    fake = MagicMock(token=_FAKE_GHS, expires_at=1_700_000_000.0)
    with patch("staff.independent_exact_head_verdict.github_app_auth_configured", return_value=True):
        with patch("staff.independent_exact_head_verdict.mint_installation_access_token", return_value=fake):
            with patch("staff.independent_exact_head_verdict.post_pr_comment") as post:
                with patch("staff.staff_github_pr_review.ensure_runner_dashboard_formal_approve"):
                    publish_independent_exact_head_verdict(
                        repo="Runner_Dashboard",
                        pr_number=9,
                        verdict="PASS",
                        head_sha="deadbeef",
                        reviewer_model="gpt-5.x-codex",
                    )
    post.assert_called_once()
    assert post.call_args.kwargs["token"] == _FAKE_GHS
    assert "PASS" in post.call_args.kwargs["body"]
    assert "deadbeef" in post.call_args.kwargs["body"]
