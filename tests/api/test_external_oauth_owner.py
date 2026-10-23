"""Valid OAuth exchanges still require the configured owner at external ingress."""

import logging
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from identity import Principal
from routers import auth
from starlette.middleware.sessions import SessionMiddleware


@pytest.fixture
def flow(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, dict[str, Any], Mock, Mock]:
    monkeypatch.setenv("DASHBOARD_OWNER_GITHUB_ID", "12345")
    owner = Principal(id="owner", type="human", name="Owner", github_username="owner")
    monkeypatch.setattr(auth.identity_manager, "principals", {owner.id: owner})
    monkeypatch.setattr(
        auth,
        "_oauth_config_for_request",
        lambda _: SimpleNamespace(github_org="D-sorganization", public_origin="https://ops.example.invalid"),
    )
    monkeypatch.setattr(auth, "_require_production_oauth", lambda _: None)
    monkeypatch.setattr(auth, "check_auth_rate_limit", lambda _: None)
    user: dict[str, Any] = {"login": "owner", "id": 12345}
    remote = AsyncMock()
    remote.get.side_effect = [Mock(json=lambda: user), Mock(status_code=204)]
    remote.__aenter__.return_value = remote
    monkeypatch.setattr(httpx, "AsyncClient", lambda: remote)
    exchange = Mock(return_value={"access_token": "test-value-must-not-be-logged"})
    persist, register = Mock(), Mock(return_value="sess-owner-test")
    monkeypatch.setattr("github_app_user_oauth.exchange_authorization_code", exchange)
    monkeypatch.setattr("github_app_user_oauth.apply_authorization_tokens", persist)
    monkeypatch.setattr(auth.sm, "register_session", register)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-key", https_only=True)  # pragma: allowlist secret
    app.include_router(auth.router)

    @app.get("/test-start")
    async def start(request: Request) -> dict[str, bool]:
        request.session.update(
            oauth_state="valid-state", oauth_state_ts=time.time(), oauth_origin="https://ops.example.invalid"
        )
        return {"ready": True}

    @app.get("/test-session")
    async def session(request: Request) -> dict[str, Any]:
        return dict(request.session)

    client = TestClient(app, base_url="https://ops.example.invalid", follow_redirects=False)
    client.get("/test-start")
    return client, user, persist, register


@pytest.mark.parametrize("github_id", [999, None, True, "12345", 12345.0])
def test_non_owner_or_malformed_id_is_rejected_before_token_or_session_writes(
    flow: tuple, github_id: Any, caplog: pytest.LogCaptureFixture
) -> None:
    client, user, persist, register = flow
    user["id"] = github_id
    with caplog.at_level(logging.WARNING):
        response = client.get("/api/auth/callback?code=valid-code&state=valid-state", headers={"CF-Ray": "test-ray"})
    assert response.status_code == 403 and response.json() == {"detail": "Request not permitted"}
    persist.assert_not_called()
    register.assert_not_called()
    assert "principal_id" not in client.get("/test-session").json()
    assert "test-value-must-not-be-logged" not in caplog.text
    records = [record for record in caplog.records if getattr(record, "reason", "") == "external_oauth_owner_denied"]
    assert records and records[0].request_id and records[0].cf_ray == "test-ray"


@pytest.mark.parametrize("configured", [None, "", "0", "012345", "+12345", "１２３４５"])
def test_missing_or_invalid_owner_configuration_fails_closed(
    flow: tuple, monkeypatch: pytest.MonkeyPatch, configured: str | None
) -> None:
    client, _, persist, register = flow
    if configured is None:
        monkeypatch.delenv("DASHBOARD_OWNER_GITHUB_ID")
    else:
        monkeypatch.setenv("DASHBOARD_OWNER_GITHUB_ID", configured)
    response = client.get("/api/auth/callback?code=valid-code&state=valid-state", headers={"CF-Ray": "test-ray"})
    assert response.status_code == 403
    persist.assert_not_called()
    register.assert_not_called()


def test_owner_session_records_immutable_id(flow: tuple) -> None:
    client, _, persist, register = flow
    response = client.get("/api/auth/callback?code=valid-code&state=valid-state", headers={"CF-Ray": "test-ray"})
    assert response.status_code == 307
    persist.assert_called_once()
    register.assert_called_once()
    session = client.get("/test-session").json()
    assert session["github_user_id"] == "12345" and session["principal_id"] == "owner"


def test_public_host_without_edge_marker_still_checks_owner(flow: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    client, user, persist, register = flow
    monkeypatch.setenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN", "https://ops.example.invalid")
    user["id"] = 999
    assert client.get("/api/auth/callback?code=valid-code&state=valid-state").status_code == 403
    persist.assert_not_called()
    register.assert_not_called()


def test_external_bot_principal_cannot_gain_a_browser_session(flow: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, persist, register = flow
    monkeypatch.setattr(auth.identity_manager.principals["owner"], "type", "bot")
    assert (
        client.get("/api/auth/callback?code=valid-code&state=valid-state", headers={"CF-Ray": "test-ray"}).status_code
        == 403
    )
    persist.assert_not_called()
    register.assert_not_called()


def test_local_callback_remains_compatible_without_external_owner_config(
    flow: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, persist, register = flow
    monkeypatch.delenv("DASHBOARD_OWNER_GITHUB_ID")
    assert client.get("/api/auth/callback?code=valid-code&state=valid-state").status_code == 307
    persist.assert_called_once()
    register.assert_called_once()
