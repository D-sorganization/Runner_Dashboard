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


pytestmark = pytest.mark.usefixtures("mock_registry_hub_as_example")


@pytest.fixture(autouse=True)
def _production_oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the exact production contract for callback-focused tests."""
    origin = "https://hub-a.fleet-example.ts.net"
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Ov23liCallbackTests")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "s" * 40)  # pragma: allowlist secret
    monkeypatch.setenv("GITHUB_ORG", "D-sorganization")
    monkeypatch.setenv("DASHBOARD_PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("GITHUB_OAUTH_CALLBACK_URL", f"{origin}/api/auth/callback")
    monkeypatch.setenv("DASHBOARD_TLS", "1")
    monkeypatch.setenv("SESSION_SECRET", "c" * 64)  # pragma: allowlist secret
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

    req = _mock_request({"oauth_state": "st2", "oauth_state_ts": time.time()})

    with patch(
        "github_app_user_oauth.exchange_authorization_code",
        side_effect=RuntimeError("malformed"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(github_callback(req, code="code", state="st2"))

    assert exc_info.value.status_code == 502
    assert "oauth_state" not in req.session


def test_oauth_callback_network_error_returns_502() -> None:
    """Network failure during token exchange → 502 and state cleared (issue #354)."""
    import httpx
    from fastapi import HTTPException
    from routers.auth import github_callback

    req = _mock_request({"oauth_state": "st3", "oauth_state_ts": time.time()})

    with patch(
        "github_app_user_oauth.exchange_authorization_code",
        side_effect=httpx.ConnectError("refused"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(github_callback(req, code="code", state="st3"))

    assert exc_info.value.status_code == 502
    assert "oauth_state" not in req.session


# ---------------------------------------------------------------------------
# Org membership check (issue #354)
# ---------------------------------------------------------------------------


def test_oauth_callback_checks_the_configured_org_for_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured org, rather than a source constant, controls admission."""
    from fastapi import HTTPException
    from routers.auth import github_callback

    monkeypatch.setenv("GITHUB_ORG", "example-org")

    token_bundle = {
        "access_token": "ghu_" + "T" * 36,
        "refresh_token": "ghr_" + "R" * 36,
        "expires_in": 28800,
        "refresh_token_expires_in": 15897600,
    }

    user_resp = MagicMock()
    user_resp.json.return_value = {"login": "outsider"}

    membership_resp = MagicMock()
    membership_resp.status_code = 404  # not a member

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=[user_resp, membership_resp])
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    req = _mock_request({"oauth_state": "st4", "oauth_state_ts": time.time()})

    with patch("github_app_user_oauth.exchange_authorization_code", return_value=token_bundle):
        with patch("httpx.AsyncClient", return_value=mock_cm):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(github_callback(req, code="code", state="st4"))

    assert exc_info.value.status_code == 403
    assert "example-org" in exc_info.value.detail
    assert mock_client.get.await_args_list[1].args[0] == "https://api.github.com/orgs/example-org/members/outsider"
    assert "oauth_state" not in req.session


def test_oauth_callback_rejects_missing_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException
    from github_app_user_oauth import UserOAuthRevokedError
    from routers.auth import github_callback

    req = _mock_request({"oauth_state": "st5", "oauth_state_ts": time.time()})
    with patch("routers.auth.check_auth_rate_limit"):
        with patch(
            "github_app_user_oauth.exchange_authorization_code",
            side_effect=UserOAuthRevokedError("missing refresh_token"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(github_callback(req, code="code", state="st5"))
    assert exc_info.value.status_code == 503
