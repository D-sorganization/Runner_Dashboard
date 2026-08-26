"""Production OAuth readiness contracts for the OGLaptop Tailscale origin.

Issue #1141 requires browser authentication to fail closed until an
administrator has installed a dedicated GitHub OAuth credential and the exact
MagicDNS callback.  These tests intentionally exercise only redacted
configuration and route behaviour; secret values must never enter diagnostics.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_ORIGIN = "https://oglaptop.tail2bbcc7.ts.net"
_CALLBACK = f"{_ORIGIN}/api/auth/callback"
_CLIENT_ID = "Ov23liProductionClient"
_CLIENT_SECRET = "s" * 40
_SESSION_SECRET = "c" * 64


def _production_env() -> dict[str, str]:
    return {
        "GITHUB_CLIENT_ID": _CLIENT_ID,
        "GITHUB_CLIENT_SECRET": _CLIENT_SECRET,
        "GITHUB_ORG": "D-sorganization",
        "DASHBOARD_PUBLIC_ORIGIN": _ORIGIN,
        "GITHUB_OAUTH_CALLBACK_URL": _CALLBACK,
        "DASHBOARD_TLS": "1",
        "SESSION_SECRET": _SESSION_SECRET,
    }


def _request() -> MagicMock:
    request = MagicMock()
    request.session = {}
    request.headers = {}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


def test_exact_production_configuration_is_ready_and_redacted() -> None:
    from oauth_config import OAuthConfig

    config = OAuthConfig.from_env(_production_env())
    diagnostic = config.diagnostic()

    assert diagnostic == {"ready": True, "status": "ready", "reason": "configured"}
    serialized = repr(diagnostic)
    assert _CLIENT_ID not in serialized
    assert _CLIENT_SECRET not in serialized
    assert _SESSION_SECRET not in serialized


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"GITHUB_CLIENT_SECRET": "short"}, "github_client_secret_invalid"),
        ({"DASHBOARD_PUBLIC_ORIGIN": "https://100.125.64.108"}, "public_origin_mismatch"),
        (
            {"GITHUB_OAUTH_CALLBACK_URL": "https://evil.example/api/auth/callback"},
            "callback_url_mismatch",
        ),
        ({"DASHBOARD_TLS": "0"}, "tls_required"),
        ({"DASHBOARD_DEV_LOGIN": "1"}, "development_auth_must_be_unset"),
        ({"DASHBOARD_LOOPBACK_AUTH": "0"}, "development_auth_must_be_unset"),
    ],
)
def test_unsafe_or_malformed_configuration_fails_closed(
    updates: dict[str, str],
    reason: str,
) -> None:
    from oauth_config import OAuthConfig

    env = _production_env()
    env.update(updates)
    diagnostic = OAuthConfig.from_env(env).diagnostic()

    assert diagnostic["ready"] is False
    assert diagnostic["status"] == "blocked"
    assert reason in diagnostic["reason"]
    assert _CLIENT_ID not in repr(diagnostic)
    assert _CLIENT_SECRET not in repr(diagnostic)


def test_missing_oauth_configuration_never_redirects_to_dev_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException
    from routers.auth import github_login

    for name in _production_env():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHBOARD_DEV_LOGIN", "1")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(github_login(_request()))

    assert exc_info.value.status_code == 503
    assert "dev-login" not in repr(exc_info.value.detail)
    assert _CLIENT_SECRET not in repr(exc_info.value.detail)


def test_login_redirect_uses_exact_callback_and_least_privilege_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers.auth import github_login

    for name, value in _production_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DASHBOARD_DEV_LOGIN", raising=False)
    monkeypatch.delenv("DASHBOARD_LOOPBACK_AUTH", raising=False)

    response = asyncio.run(github_login(_request()))
    query = parse_qs(urlparse(response.headers["location"]).query)

    assert query["client_id"] == [_CLIENT_ID]
    assert query["redirect_uri"] == [_CALLBACK]
    assert query["scope"] == ["read:user"]
    assert len(query["state"][0]) >= 20


def test_health_diagnostic_contract_contains_no_credential_fields() -> None:
    from oauth_config import OAuthConfig

    diagnostic = OAuthConfig.from_env(_production_env()).diagnostic()

    assert set(diagnostic) == {"ready", "status", "reason"}
    assert not any("client" in key or "secret" in key or "token" in key for key in diagnostic)

