"""Authorization and code exchange use one explicitly configured browser origin."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from identity import Principal
from oauth_config import OAuthConfig
from routers import auth
from starlette.middleware.sessions import SessionMiddleware

pytestmark = pytest.mark.usefixtures("mock_registry_hub_as_example")

PRIVATE = "https://hub-a.fleet-example.ts.net"
PUBLIC = "https://ops.example.invalid"
OTHER = "https://other.example.invalid"
EDGE = {"CF-Ray": "test-ray"}


@pytest.fixture
def flow(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Mock, dict[str, str]]:
    env = {
        "GITHUB_CLIENT_ID": "12345678",
        "GITHUB_CLIENT_SECRET": "a" * 32,
        "GITHUB_ORG": "D-sorganization",
        "DASHBOARD_PUBLIC_ORIGIN": PRIVATE,
        "GITHUB_OAUTH_CALLBACK_URL": PRIVATE + "/api/auth/callback",
        "SESSION_SECRET": "b" * 32,
        "DASHBOARD_TLS": "1",
        "DASHBOARD_OAUTH_ALLOWED_ORIGINS": f"{PUBLIC},{OTHER}",
    }
    monkeypatch.setenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN", PUBLIC)
    monkeypatch.setenv("DASHBOARD_OWNER_GITHUB_ID", "12345")
    monkeypatch.setattr(auth, "_production_oauth_config", lambda: OAuthConfig.from_env(env))
    monkeypatch.setattr(auth, "check_auth_rate_limit", lambda _: None)
    owner = Principal(id="owner", type="human", name="Owner", github_username="owner")
    monkeypatch.setattr(auth.identity_manager, "principals", {owner.id: owner})
    remote = AsyncMock()
    remote.get.side_effect = [Mock(json=lambda: {"login": "owner", "id": 12345}), Mock(status_code=204)]
    remote.__aenter__.return_value = remote
    monkeypatch.setattr(httpx, "AsyncClient", lambda: remote)
    exchange = Mock(return_value={"access_token": "test-value"})
    monkeypatch.setattr("github_app_user_oauth.exchange_authorization_code", exchange)
    monkeypatch.setattr("github_app_user_oauth.apply_authorization_tokens", Mock())
    monkeypatch.setattr(auth.sm, "register_session", Mock(return_value="sess-owner-test"))
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-key", https_only=True)  # pragma: allowlist secret
    app.include_router(auth.router)

    @app.get("/test-session")
    async def session(request: Request) -> dict[str, Any]:
        return dict(request.session)

    return TestClient(app, base_url=PRIVATE, follow_redirects=False), exchange, env


def start(client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
    response = client.get("/api/auth/github", headers=headers)
    assert response.status_code == 307
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return query["state"][0], query["redirect_uri"][0]


def test_external_authorization_and_exchange_use_public_callback(flow: tuple) -> None:
    client, exchange, _ = flow
    state, callback = start(client, EDGE)
    assert callback == PUBLIC + "/api/auth/callback"
    response = client.get("/api/auth/callback", params={"code": "valid-code", "state": state}, headers=EDGE)
    assert response.status_code == 307
    config = exchange.call_args.args[1]
    assert config.token_exchange_data("valid-code")["redirect_uri"] == callback
    assert "oauth_origin" not in client.get("/test-session").json()


def test_private_authorization_and_exchange_keep_magicdns_callback(flow: tuple) -> None:
    client, exchange, _ = flow
    state, callback = start(client, {})
    assert callback == PRIVATE + "/api/auth/callback"
    assert client.get("/api/auth/callback", params={"code": "valid-code", "state": state}).status_code == 307
    assert exchange.call_args.args[1].callback_url == callback


@pytest.mark.parametrize("origin", [None, "http://ops.example.invalid", PUBLIC + "/path", "https://user@ops.invalid"])
def test_external_signin_requires_valid_explicit_browser_origin(
    flow: tuple, monkeypatch: pytest.MonkeyPatch, origin: str | None
) -> None:
    client, exchange, _ = flow
    if origin is None:
        monkeypatch.delenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN")
    else:
        monkeypatch.setenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN", origin)
    response = client.get("/api/auth/github", headers=EDGE)
    assert response.status_code == 503
    assert "oauth_state" not in client.get("/test-session").json()
    exchange.assert_not_called()


def test_browser_callback_still_requires_oauth_origin_allowlist(flow: tuple) -> None:
    client, exchange, env = flow
    env["DASHBOARD_OAUTH_ALLOWED_ORIGINS"] = ""
    assert client.get("/api/auth/github", headers=EDGE).status_code == 503
    exchange.assert_not_called()


@pytest.mark.parametrize("change", ["origin", "plane"])
def test_origin_or_plane_change_consumes_state_without_exchanging_code(
    flow: tuple, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    client, exchange, _ = flow
    state, _ = start(client, EDGE)
    if change == "origin":
        monkeypatch.setenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN", OTHER)
    response = client.get(
        "/api/auth/callback", params={"code": "valid-code", "state": state}, headers=EDGE if change == "origin" else {}
    )
    assert response.status_code == 400
    exchange.assert_not_called()
    session = client.get("/test-session").json()
    assert not {"oauth_state", "oauth_state_ts", "oauth_origin"}.intersection(session)


def test_untrusted_host_does_not_choose_callback(flow: tuple) -> None:
    client, _, _ = flow
    _, callback = start(client, {**EDGE, "Host": "attacker.example.invalid"})
    assert callback == PUBLIC + "/api/auth/callback"
