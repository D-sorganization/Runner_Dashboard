"""Subscription-only credential probes and retired pay-per-use key endpoints (#2170)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from routers.credentials import _probe_antigravity_cli, _probe_claude_code_cli, _probe_codex_cli, _probe_jules_api


@pytest.fixture
def local_credentials_client() -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app)


@pytest.mark.unit
def test_set_key_endpoint_removed(local_credentials_client: TestClient) -> None:
    resp = local_credentials_client.post(
        "/api/credentials/set-key",
        json={"provider": "claude", "key": "secret", "restart_maxwell": False},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code in (404, 405)


@pytest.mark.unit
def test_clear_key_endpoint_removed(local_credentials_client: TestClient) -> None:
    resp = local_credentials_client.post(
        "/api/credentials/clear-key",
        json={"provider": "claude", "restart_maxwell": False},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code in (404, 405)


@pytest.mark.unit
def test_antigravity_ignores_api_key_env_without_google_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "pay-per-use-key")
    monkeypatch.setenv("ANTIGRAVITY_API_KEY", "pay-per-use-key")
    with (
        patch("routers.credentials._find_binary", return_value="/usr/bin/agy"),
        patch("routers.credentials._antigravity_login_present", return_value=False),
        patch(
            "staff.cli_oauth_session.antigravity_oauth_session_alive",
            return_value=(False, "not signed in"),
        ),
        patch("staff.quota.headroom", return_value=(True, "ok")),
    ):
        probe = _probe_antigravity_cli()
    assert probe["authenticated"] is False
    assert probe["status"] == "missing_key"


@pytest.mark.unit
def test_jules_api_ignores_google_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JULES_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-only")
    probe = _probe_jules_api()
    assert probe["authenticated"] is False
    assert probe["status"] == "missing_key"


@pytest.mark.unit
def test_codex_probe_requires_chatgpt_login_not_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("CODEX_HOME", raising=False)
    with patch("routers.credentials._find_binary", return_value="/usr/bin/codex"):
        with patch("routers.credentials._cli_login_file_present", return_value=False):
            probe = _probe_codex_cli()
    assert probe["authenticated"] is False


@pytest.mark.unit
def test_claude_probe_requires_oauth_not_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import credentials as cred_mod  # noqa: PLC0415

    monkeypatch.setattr(cred_mod, "_env_present_anywhere", lambda _k: True)
    with (
        patch("routers.credentials.shutil.which", return_value="/usr/bin/claude"),
        patch("routers.credentials._claude_login_file_present", return_value=False),
        patch(
            "staff.cli_oauth_session.claude_oauth_session_alive",
            return_value=(False, "not signed in"),
        ),
    ):
        probe = _probe_claude_code_cli()
    assert probe["authenticated"] is False
