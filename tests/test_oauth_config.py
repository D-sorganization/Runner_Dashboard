"""Unit tests for backend.oauth_config (Issue #386 compliance)."""

from __future__ import annotations

import pytest
from oauth_config import (
    OAUTH_CALLBACK_PATH,
    OAuthConfig,
    OAuthConfigurationError,
    validate_oauth_startup,
)

pytestmark = pytest.mark.usefixtures("mock_registry_hub_as_example")

_DESK_ORIGIN = "https://hub-a.fleet-example.ts.net"
_DESK_CALLBACK = f"{_DESK_ORIGIN}{OAUTH_CALLBACK_PATH}"


def _valid_env() -> dict[str, str]:
    return {
        "GITHUB_CLIENT_ID": "12345678",
        "GITHUB_CLIENT_SECRET": "a" * 32,
        "GITHUB_ORG": "D-sorganization",
        "DASHBOARD_PUBLIC_ORIGIN": _DESK_ORIGIN,
        "GITHUB_OAUTH_CALLBACK_URL": _DESK_CALLBACK,
        "DASHBOARD_OAUTH_ALLOWED_ORIGINS": _DESK_ORIGIN,
        "SESSION_SECRET": "b" * 32,
        "DASHBOARD_TLS": "1",
    }


def test_oauth_config_ready() -> None:
    cfg = OAuthConfig.from_env(_valid_env())
    diag = cfg.diagnostic()
    assert diag["ready"] is True
    assert diag["status"] == "ready"
    assert diag["reason"] == "configured"
    assert diag["flow"] == "github_app_user_to_server"
    assert cfg.client_id_configured is True

    # repr check
    repr_str = repr(cfg)
    assert "12345678" not in repr_str
    assert "a" * 32 not in repr_str

    # authorization url
    auth_url = cfg.authorization_url("state_token_1234567890")
    assert "client_id=12345678" in auth_url
    assert "redirect_uri=" in auth_url

    # token exchange
    token_data = cfg.token_exchange_data("auth_code_123")
    assert token_data["client_id"] == "12345678"
    assert token_data["code"] == "auth_code_123"


def test_oauth_config_blocked_missing_fields() -> None:
    cfg = OAuthConfig.from_env({})
    diag = cfg.diagnostic()
    assert diag["ready"] is False
    assert diag["status"] == "blocked"

    with pytest.raises(OAuthConfigurationError):
        cfg.require_ready()


def test_oauth_config_uses_configured_github_organization() -> None:
    env = _valid_env()
    env["GITHUB_ORG"] = "example-org"

    cfg = OAuthConfig.from_env(env)

    assert cfg.diagnostic()["ready"] is True
    assert cfg.github_org == "example-org"


def test_oauth_config_fails_closed_when_github_organization_is_missing() -> None:
    env = _valid_env()
    env.pop("GITHUB_ORG")

    cfg = OAuthConfig.from_env(env)

    assert cfg.diagnostic()["reason"] == "github_org_missing"
    with pytest.raises(OAuthConfigurationError):
        cfg.require_ready()


def test_oauth_startup_requires_organization_only_when_oauth_is_enabled() -> None:
    validate_oauth_startup(OAuthConfig.from_env({}))

    with pytest.raises(RuntimeError, match="GITHUB_ORG"):
        validate_oauth_startup(OAuthConfig.from_env({"GITHUB_CLIENT_ID": "configured"}))


def test_oauth_config_rejects_invalid_organization_names() -> None:
    env = _valid_env()
    env["GITHUB_ORG"] = "example/org"

    cfg = OAuthConfig.from_env(env)

    assert cfg.diagnostic()["reason"] == "github_org_invalid"


def test_oauth_config_invalid_state_and_code() -> None:
    cfg = OAuthConfig.from_env(_valid_env())
    with pytest.raises(ValueError, match="OAuth state must be at least 20"):
        cfg.authorization_url("short")

    with pytest.raises(ValueError, match="OAuth code must be a non-empty"):
        cfg.token_exchange_data("   ")
