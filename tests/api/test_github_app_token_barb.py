"""Hub-mediated GitHub App tokens for Barb lane principals (#3657)."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import identity
import pytest
from fastapi.testclient import TestClient
from identity import Principal
from server import app
from staff.github_run_env import StaffGithubToken
from staff.rate_limit import TokenBucketLimiter, reset_rate_limiter

_FAKE_GHS = "ghs_" + "B" * 40 + "_barbmint"
_BARB_TOKEN = "svc_barb_github_app_ci"  # pragma: allowlist secret
_XHR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def auth_barb(monkeypatch: pytest.MonkeyPatch) -> None:
    prin = Principal(
        id="agent-grok-vm",
        type="bot",
        name="Barb VM",
        roles=["bot"],
        scopes=["github.app_token", "fleet.read"],
    )
    monkeypatch.setattr(identity.identity_manager, "verify_token", lambda raw: prin if raw == _BARB_TOKEN else None)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_BARB_TOKEN}", **_XHR}


def _hub(monkeypatch: pytest.MonkeyPatch) -> None:
    import dashboard_config

    monkeypatch.setattr(dashboard_config, "MACHINE_ROLE", "hub")


def test_github_app_token_auth_and_scope(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _hub(monkeypatch)
    assert client.post("/api/github/app-token", json={}, headers=_XHR).status_code == 401
    narrow = Principal(id="x", type="bot", name="x", scopes=["fleet.read"])
    monkeypatch.setattr(identity.identity_manager, "verify_token", lambda raw: narrow if raw == _BARB_TOKEN else None)
    assert client.post("/api/github/app-token", json={}, headers=_headers()).status_code == 403


def test_github_app_token_mints(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _hub(monkeypatch)
    now = time.time()
    fake = StaffGithubToken(token=_FAKE_GHS, expires_at=now + 3600.0, source="github_app")
    with patch("routers.github_app_token.mint_barb_github_app_token", return_value=fake):
        resp = client.post("/api/github/app-token", json={"repository": "Runner_Dashboard"}, headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["token"] == _FAKE_GHS


def test_github_app_token_rate_limited(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _hub(monkeypatch)
    reset_rate_limiter()
    lim = TokenBucketLimiter(capacity={"github_app_token": 1.0}, rate_per_sec={"github_app_token": 0.001})
    monkeypatch.setattr("routers.github_app_token.get_rate_limiter", lambda: lim)
    fake = StaffGithubToken(token=_FAKE_GHS, expires_at=time.time() + 60.0, source="github_app")
    with patch("routers.github_app_token.mint_barb_github_app_token", return_value=fake):
        assert client.post("/api/github/app-token", json={}, headers=_headers()).status_code == 200
        assert client.post("/api/github/app-token", json={}, headers=_headers()).status_code == 429


def test_barb_cli_gh_token(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts import barb_cli

    with patch(
        "scripts.barb_github_hub_token.fetch_hub_github_app_token",
        return_value=(_FAKE_GHS, time.time() + 120.0),
    ):
        assert barb_cli.main(["gh-token"]) == 0
    assert capsys.readouterr().out.strip() == _FAKE_GHS


def test_github_app_token_accepts_missing_body(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Older barb-worker helpers POST without a body; the hub must not 422."""
    _hub(monkeypatch)
    with patch("routers.github_app_token.mint_barb_github_app_token", return_value=StaffGithubToken(token=_FAKE_GHS, expires_at=time.time() + 600, source="github_app")):
        resp = client.post("/api/github/app-token", headers=_headers())
    assert resp.status_code == 200


def test_hub_token_helper_always_sends_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import barb_github_hub_token as mod

    sent: dict[str, Any] = {}

    class _Client:
        def __init__(self, token: str) -> None:
            pass

        def request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
            sent["body"] = body
            return {"token": "ghs_x", "expires_at": 1.0}

    import fleet_client

    monkeypatch.setenv("FLEET_API_TOKEN", "svc_test")
    monkeypatch.setattr(fleet_client, "FleetClient", _Client)
    assert mod.fetch_hub_github_app_token()[0] == "ghs_x"
    assert sent["body"] == {}
