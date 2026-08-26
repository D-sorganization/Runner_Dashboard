"""Tests for GitHub OAuth callback security fixes (issue #354)."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def _mock_request(session: dict) -> MagicMock:
    """Build a minimal mock Request with the given session dict."""
    req = MagicMock()
    req.session = dict(session)
    req.headers = {}
    req.client = MagicMock()
    req.client.host = "10.0.0.1"
    return req


@pytest.fixture(autouse=True)
def _production_oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the exact production contract for callback-focused tests."""
    origin = "https://oglaptop.tail2bbcc7.ts.net"
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Ov23liCallbackTests")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "s" * 40)
    monkeypatch.setenv("GITHUB_ORG", "D-sorganization")
    monkeypatch.setenv("DASHBOARD_PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("GITHUB_OAUTH_CALLBACK_URL", f"{origin}/api/auth/callback")
    monkeypatch.setenv("DASHBOARD_TLS", "1")
    monkeypatch.setenv("SESSION_SECRET", "c" * 64)
    monkeypatch.delenv("DASHBOARD_DEV_LOGIN", raising=False)
    monkeypatch.delenv("DASHBOARD_LOOPBACK_AUTH", raising=False)


# ---------------------------------------------------------------------------
# State validity and expiry (issue #354)
# ---------------------------------------------------------------------------


def test_oauth_callback_rejects_invalid_state() -> None:
    """Mismatched state is rejected with 400 and state is cleared (issue #354)."""
    from fastapi import HTTPException
    from routers.auth import github_callback

    req = _mock_request({"oauth_state": "expected", "oauth_state_ts": time.time()})
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(github_callback(req, code="code", state="wrong"))
    assert exc_info.value.status_code == 400
    assert "oauth_state" not in req.session


def test_oauth_callback_rejects_expired_state() -> None:
    """State older than TTL is rejected with 400 and cleared (issue #354)."""
    from fastapi import HTTPException
    from routers import auth as auth_router
    from routers.auth import github_callback

    stale_ts = time.time() - auth_router._OAUTH_STATE_TTL_SECONDS - 10
    req = _mock_request({"oauth_state": "st", "oauth_state_ts": stale_ts})
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(github_callback(req, code="code", state="st"))
    assert exc_info.value.status_code == 400
    assert "expired" in exc_info.value.detail.lower()
    assert "oauth_state" not in req.session


# ---------------------------------------------------------------------------
# Token-exchange error handling → 502, state cleared (issue #354)
# ---------------------------------------------------------------------------


def test_oauth_callback_malformed_token_response_returns_502() -> None:
    """Malformed token-exchange body → 502 and state is cleared (issue #354)."""
    from fastapi import HTTPException
    from routers.auth import github_callback

    bad_resp = MagicMock()
    bad_resp.status_code = 200
    bad_resp.json.side_effect = ValueError("not json")

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=bad_resp)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    req = _mock_request({"oauth_state": "st2", "oauth_state_ts": time.time()})

    with patch("httpx.AsyncClient", return_value=mock_cm):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(github_callback(req, code="code", state="st2"))

    assert exc_info.value.status_code == 502
    assert "oauth_state" not in req.session


def test_oauth_callback_network_error_returns_502() -> None:
    """Network failure during token exchange → 502 and state cleared (issue #354)."""
    import httpx
    from fastapi import HTTPException
    from routers.auth import github_callback

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    req = _mock_request({"oauth_state": "st3", "oauth_state_ts": time.time()})

    with patch("httpx.AsyncClient", return_value=mock_cm):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(github_callback(req, code="code", state="st3"))

    assert exc_info.value.status_code == 502
    assert "oauth_state" not in req.session


# ---------------------------------------------------------------------------
# Org membership check (issue #354)
# ---------------------------------------------------------------------------


def test_oauth_callback_non_member_rejected_with_403() -> None:
    """Non-org member is rejected with 403 for the production org."""
    from fastapi import HTTPException
    from routers.auth import github_callback

    token_resp = MagicMock()
    token_resp.json.return_value = {"access_token": "tok123"}

    user_resp = MagicMock()
    user_resp.json.return_value = {"login": "outsider"}

    membership_resp = MagicMock()
    membership_resp.status_code = 404  # not a member

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=token_resp)
    mock_client.get = AsyncMock(side_effect=[user_resp, membership_resp])
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    req = _mock_request({"oauth_state": "st4", "oauth_state_ts": time.time()})

    with patch("httpx.AsyncClient", return_value=mock_cm):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(github_callback(req, code="code", state="st4"))

    assert exc_info.value.status_code == 403
    assert "D-sorganization" in exc_info.value.detail
    assert "oauth_state" not in req.session
