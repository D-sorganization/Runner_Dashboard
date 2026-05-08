"""Tests for backend/gh_utils.py — issue #386."""

from __future__ import annotations

import gh_utils as gu


def test_clear_rate_limit_breakers_no_error() -> None:
    """clear_rate_limit_breakers must not raise."""
    gu.clear_rate_limit_breakers()


def test_github_token_fingerprint_anonymous(monkeypatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = gu._github_token_fingerprint()
    assert result == "anonymous"


def test_github_token_fingerprint_with_token(monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "my-secret-token")
    result = gu._github_token_fingerprint()
    assert len(result) == 16
    assert result != "anonymous"


def test_resource_class_actions_endpoint() -> None:
    assert gu._resource_class("/repos/org/repo/actions/runs") == "actions"


def test_resource_class_repos_endpoint() -> None:
    assert gu._resource_class("/repos/org/repo") == "repos"


def test_resource_class_unknown_endpoint() -> None:
    result = gu._resource_class("/orgs/my-org/members")
    assert isinstance(result, str)


def test_rate_limited_error_attributes() -> None:
    err = gu.RateLimitedError(
        retry_after_seconds=60,
        endpoint="/repos/foo/bar",
        resource_class="repos",
        detail="Rate limit exceeded",
    )
    assert err.retry_after_seconds == 60
    assert err.endpoint == "/repos/foo/bar"
    assert err.resource_class == "repos"


def test_rate_limited_error_min_retry() -> None:
    """retry_after_seconds must be at least 1."""
    err = gu.RateLimitedError(retry_after_seconds=0, endpoint="/x", resource_class="core")
    assert err.retry_after_seconds >= 1
