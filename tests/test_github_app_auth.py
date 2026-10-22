"""Tests for shared GitHub App installation-token minting (#2038)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from github_app_auth import (
    InstallationToken,
    build_app_jwt,
    github_app_auth_configured,
    mint_installation_access_token,
)


def test_github_app_auth_configured_false_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_FILE", raising=False)
    assert github_app_auth_configured() is False


def test_mint_installation_access_token_requires_app_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    with pytest.raises(Exception, match="not configured"):
        mint_installation_access_token()


def test_mint_installation_access_token_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "fake-key")  # pragma: allowlist secret

    class _Resp:
        status_code = 201

        @staticmethod
        def json() -> dict[str, str]:
            return {"token": "ghs_" + "x" * 40, "expires_at": "2030-01-01T00:00:00Z"}

    from unittest.mock import MagicMock

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"permissions": {"contents": "read", "metadata": "read"}}

    with patch("github_app_auth.github_app_private_key_configured", return_value=True):
        with patch("github_app_auth.build_app_jwt", return_value="jwt"):
            with patch("httpx.Client") as client_cls:
                client = client_cls.return_value.__enter__.return_value
                client.get.return_value = get_resp
                client.post.return_value = _Resp()
                tok = mint_installation_access_token(repositories=["org/repo"], permissions={"contents": "read"})
    assert isinstance(tok, InstallationToken)
    assert tok.token.startswith("ghs_")


def test_build_app_jwt_uses_rs256(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv("GITHUB_APP_ID", "99")
    with patch("github_app_auth.github_app_private_key", return_value=pem):
        token = build_app_jwt()
    assert isinstance(token, str)
    assert token.count(".") == 2
