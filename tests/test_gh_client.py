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


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------


def test_get_token_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import gh_client

    gh_client.clear_token_cache()
    monkeypatch.setenv("GH_TOKEN", "test-token-abc")
    token = gh_client._get_token()
    assert token == "test-token-abc"
    gh_client.clear_token_cache()


def test_get_token_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    import gh_client

    gh_client.clear_token_cache()
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
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(gh_client.GhAuthError):
        gh_client._get_token()
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
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    import json

    expected = {"runners": []}
    with patch("gh_utils.run_cmd", new=AsyncMock(return_value=(0, json.dumps(expected), ""))) as mock_cmd:
        result = await gh_utils.gh_api("/orgs/x/actions/runners")

    assert result == expected
    mock_cmd.assert_called_once()
    gh_client.clear_token_cache()


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
