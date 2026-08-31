"""Unit tests for backend.oauth_config (Issue #386 compliance)."""

from __future__ import annotations

import pytest
from oauth_config import (
    EXPECTED_CALLBACK_URL,
    EXPECTED_GITHUB_ORG,
    EXPECTED_PUBLIC_ORIGIN,
    OAuthConfig,
    OAuthConfigurationError,
)


def _valid_env() -> dict[str, str]:
    return {
        "GITHUB_CLIENT_ID": "12345678",
        "GITHUB_CLIENT_SECRET": "a" * 32,
        "GITHUB_ORG": EXPECTED_GITHUB_ORG,
        "DASHBOARD_PUBLIC_ORIGIN": EXPECTED_PUBLIC_ORIGIN,
        "GITHUB_OAUTH_CALLBACK_URL": EXPECTED_CALLBACK_URL,
        "SESSION_SECRET": "b" * 32,
        "DASHBOARD_TLS": "1",
    }


def test_oauth_config_ready() -> None:
    cfg = OAuthConfig.from_env(_valid_env())
    diag = cfg.diagnostic()
    assert diag["ready"] is True
    assert diag["status"] == "ready"
    assert diag["reason"] == "configured"
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


def test_oauth_config_invalid_state_and_code() -> None:
    cfg = OAuthConfig.from_env(_valid_env())
    with pytest.raises(ValueError, match="OAuth state must be at least 20"):
        cfg.authorization_url("short")

    with pytest.raises(ValueError, match="OAuth code must be a non-empty"):
        cfg.token_exchange_data("   ")
