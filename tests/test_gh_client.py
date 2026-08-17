"""Tests for the pooled GitHub API httpx client (issue #352).

Validates:
1. Token caching (GH_TOKEN read once).
2. GhRateLimited, GhNotFound, GhServerError exception types.
3. _parse_next_link for Link header parsing.
4. close_client cleans up.
5. gh_utils.gh_api delegates to gh_client when token is present (integration).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))


@pytest.fixture(autouse=True)
def _reset_gh_rate_limit_breakers() -> None:
    import gh_utils

    gh_utils.clear_rate_limit_breakers()
    yield
    gh_utils.clear_rate_limit_breakers()


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------


def test_get_token_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import gh_client

    gh_client.clear_token_cache()
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("GH_TOKEN", "test-token-abc")
    token = gh_client._get_token()
    assert token == "test-token-abc"
    gh_client.clear_token_cache()


def test_get_token_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    import gh_client

    gh_client.clear_token_cache()
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("GH_TOKEN", "tok1")
    gh_client._get_token()  # prime cache
    # change env — should not matter because token is cached
    monkeypatch.setenv("GH_TOKEN", "tok2")
    token = gh_client._get_token()
    assert token == "tok1"
    gh_client.clear_token_cache()


def test_get_token_raises_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    import gh_client

    gh_client.clear_token_cache()
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(gh_client.GhAuthError):
        gh_client._get_token()
    gh_client.clear_token_cache()


async def test_get_token_prefers_github_app(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    import gh_client

    gh_client.clear_token_cache()
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "fake-key")
    monkeypatch.setenv("GH_TOKEN", "fallback-token")

    with patch.object(gh_client, "_fetch_github_app_installation_token", AsyncMock(return_value="installation-token")):
        assert await gh_client._get_token_async() == "installation-token"

    assert gh_client.get_status()["auth_source"] == "github_app"
    gh_client.clear_token_cache()


async def test_get_token_falls_back_when_github_app_exchange_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    import gh_client

    gh_client.clear_token_cache()
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "fake-key")
    monkeypatch.setenv("GH_TOKEN", "fallback-token")

    with patch.object(gh_client, "_fetch_github_app_installation_token", AsyncMock(side_effect=RuntimeError("boom"))):
        assert await gh_client._get_token_async() == "fallback-token"


async def test_concurrent_token_refresh_triggers_single_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #938c: a refresh storm dedupes to exactly one upstream exchange."""
    import asyncio

    import gh_client

    gh_client.clear_token_cache()
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "fake-key")

    calls = 0

    async def _slow_exchange() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)  # hold the lock so peers queue behind it
        gh_client._cached_token_expires_at = gh_client.time.time() + 3600
        return "installation-token"

    monkeypatch.setattr(gh_client, "_fetch_github_app_installation_token", _slow_exchange)

    results = await asyncio.gather(*[gh_client._get_token_async() for _ in range(10)])
    assert results == ["installation-token"] * 10
    assert calls == 1
    gh_client.clear_token_cache()


# ---------------------------------------------------------------------------
# _parse_next_link
# ---------------------------------------------------------------------------


def test_parse_next_link_present() -> None:
    from gh_client import _parse_next_link

    link = '<https://api.github.com/orgs/x/repos?page=2>; rel="next", <…>; rel="last"'
    assert _parse_next_link(link) == "https://api.github.com/orgs/x/repos?page=2"


def test_parse_next_link_absent() -> None:
    from gh_client import _parse_next_link

    assert _parse_next_link("") is None
    assert _parse_next_link('<x>; rel="last"') is None


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


def test_gh_rate_limited_has_retry_after() -> None:
    from gh_client import GhRateLimited

    exc = GhRateLimited(retry_after_seconds=120, endpoint="/orgs/x/runners")
    assert exc.retry_after_seconds == 120
    assert "/orgs/x/runners" in str(exc)


def test_gh_not_found() -> None:
    from gh_client import GhNotFound

    exc = GhNotFound("/repos/org/missing")
    assert "404" in str(exc)


def test_gh_server_error() -> None:
    from gh_client import GhServerError

    exc = GhServerError(503, "/orgs/x/runners", "Service Unavailable")
    assert exc.status_code == 503


def test_github_status_records_rate_limit() -> None:
    import httpx
    from gh_client import _is_rate_limited_response, get_status

    resp = httpx.Response(
        403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999999999"},
        text="API rate limit exceeded",
    )
    assert _is_rate_limited_response(resp) is True
    status = get_status()
    assert "status" in status


# ---------------------------------------------------------------------------
# gh_utils.gh_api delegates to gh_client when token is present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gh_utils_delegates_to_gh_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """gh_utils.gh_api should use gh_client.get() when token is available."""
    import gh_client
    import gh_utils

    gh_client.clear_token_cache()
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_data = {"runners": [{"id": 1, "name": "runner-1", "status": "online"}]}
    with patch.object(gh_client, "get", new=AsyncMock(return_value=mock_data)):
        result = await gh_utils.gh_api("/orgs/test-org/actions/runners")

    assert result == mock_data
    gh_client.clear_token_cache()


@pytest.mark.asyncio
async def test_gh_utils_falls_back_to_subprocess_when_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """gh_utils.gh_api falls back to subprocess when GH_TOKEN is absent."""
    import gh_client
    import gh_utils

    gh_client.clear_token_cache()
    gh_utils.clear_rate_limit_breakers()
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    import json

    expected = {"runners": []}
    with patch("gh_utils.run_cmd", new=AsyncMock(return_value=(0, json.dumps(expected), ""))) as mock_cmd:
        result = await gh_utils.gh_api("/orgs/x/actions/runners")

    assert result == expected
    mock_cmd.assert_called_once()
    gh_utils.clear_rate_limit_breakers()
    gh_client.clear_token_cache()
    gh_utils.clear_rate_limit_breakers()


# ---------------------------------------------------------------------------
# gh_client.py source structure checks
# ---------------------------------------------------------------------------


def test_gh_client_exports_get() -> None:
    import gh_client

    assert callable(gh_client.get)


def test_gh_client_exports_paginate() -> None:
    import gh_client

    assert callable(gh_client.paginate)


def test_gh_client_exports_cancel_run() -> None:
    import gh_client

    assert callable(gh_client.cancel_run)


def test_gh_client_exports_rerun_failed() -> None:
    import gh_client

    assert callable(gh_client.rerun_failed)


# ---------------------------------------------------------------------------
# Issue #938: gh_client robustness (202 success, paginate rate-limit, async token)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, *, json_body=None, headers=None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json


class _FakeClient:
    """Minimal async httpx.AsyncClient stand-in returning queued responses."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.headers: dict[str, str] = {}
        self.requests: list[tuple[str, str]] = []

    async def request(self, method: str, path: str, **_kwargs) -> _FakeResponse:
        self.requests.append((method, path))
        return self._responses.pop(0)

    async def get(self, url: str, **_kwargs) -> _FakeResponse:
        self.requests.append(("GET", url))
        return self._responses.pop(0)


async def test_request_accepts_202_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #938a: 202 Accepted (e.g. cancel_run) is a success, not a server error."""
    import gh_client

    gh_client.clear_token_cache()
    monkeypatch.setenv("GH_TOKEN", "t")
    fake = _FakeClient([_FakeResponse(202, json_body={"ok": True})])
    monkeypatch.setattr(gh_client, "_get_client", lambda: fake)

    resp = await gh_client._request("POST", "/repos/x/y/actions/runs/1/cancel")
    assert resp.status_code == 202
    gh_client.clear_token_cache()


async def test_paginate_raises_typed_rate_limited_on_primary_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #938b: a 403 + X-RateLimit-Remaining: 0 routes through _request.

    Before #938b paginate only special-cased 429 and raised an opaque
    GhServerError on a primary-rate-limit 403. Now it goes through _request, so
    the caller gets the typed GhRateLimited (carrying retry_after) instead.
    """
    import gh_client

    gh_client.clear_token_cache()
    monkeypatch.setenv("GH_TOKEN", "t")

    rate_limited = _FakeResponse(
        403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1"},
        text="API rate limit exceeded",
    )
    fake = _FakeClient([rate_limited])
    monkeypatch.setattr(gh_client, "_get_client", lambda: fake)

    with pytest.raises(gh_client.GhRateLimited):
        _ = [item async for item in gh_client.paginate("/orgs/x/actions/runners")]
    gh_client.clear_token_cache()


async def test_paginate_retries_transient_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #938b: a transient 5xx on a page retries (via _request) instead of aborting."""
    import gh_client

    gh_client.clear_token_cache()
    monkeypatch.setenv("GH_TOKEN", "t")

    server_err = _FakeResponse(503, headers={}, text="upstream hiccup")
    ok = _FakeResponse(200, json_body=[{"id": 7}], headers={})
    fake = _FakeClient([server_err, ok])
    monkeypatch.setattr(gh_client, "_get_client", lambda: fake)

    async def _no_sleep(_secs: float) -> None:
        return None

    monkeypatch.setattr(gh_client.asyncio, "sleep", _no_sleep)

    items = [item async for item in gh_client.paginate("/orgs/x/actions/runners")]
    assert items == [{"id": 7}]
    gh_client.clear_token_cache()
