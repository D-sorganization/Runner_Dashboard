"""External chat writes require a session- and thread-bound browser proof."""

import logging
import time

import pytest
from browser_csrf import browser_csrf_denial
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from routers.auth import router
from starlette.middleware.sessions import SessionMiddleware

_ORIGIN = "https://ops.example.invalid"
_HEADERS = {"CF-Ray": "test-edge", "Origin": _ORIGIN, "X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_PUBLIC_ORIGIN", _ORIGIN)
    app = FastAPI()
    owner = Principal(id="owner", type="human", name="Owner")
    app.dependency_overrides[require_principal] = lambda: owner
    app.include_router(router)

    @app.middleware("http")
    async def gate(request: Request, call_next):
        denial = browser_csrf_denial(request, owner)
        return denial if denial is not None else await call_next(request)

    app.add_middleware(SessionMiddleware, secret_key="test-signing-key")  # pragma: allowlist secret

    @app.post("/session/{session_id}")
    def session(request: Request, session_id: str) -> dict:
        request.session.update(session_id=session_id, principal_id="owner")
        return {}

    @app.post("/api/v1/staff/threads/{thread_id}/messages")
    @app.post("/api/v1/staff/panels")
    def mutate() -> dict:
        return {"executed": True}

    browser = TestClient(app, base_url=_ORIGIN)
    browser.post("/session/session-1")
    return browser


def _token(client: TestClient, thread: str = "th_one") -> str:
    response = client.post("/api/auth/browser-csrf", json={"thread_id": thread}, headers=_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_valid_proof_allows_exact_session_and_thread(client: TestClient) -> None:
    headers = {**_HEADERS, "X-CSRF-Token": _token(client)}
    assert client.post("/api/v1/staff/threads/th_one/messages", headers=headers).json() == {"executed": True}
    assert client.post("/api/v1/staff/threads/th_two/messages", headers=headers).status_code == 403
    client.post("/session/session-2")
    assert client.post("/api/v1/staff/threads/th_one/messages", headers=headers).status_code == 403


@pytest.mark.parametrize("missing", ["Origin", "X-Requested-With", "X-CSRF-Token"])
def test_missing_browser_proof_parts_never_execute(client: TestClient, missing: str) -> None:
    headers = {**_HEADERS, "X-CSRF-Token": _token(client)}
    headers.pop(missing)
    response = client.post("/api/v1/staff/threads/th_one/messages", headers=headers)
    assert response.status_code == 403 and response.json() == {"error": "Request not permitted"}
    assert response.headers["X-CSRF-Required"] == "1"


@pytest.mark.parametrize("origin", ["https://evil.invalid", "null", _ORIGIN + ".evil.invalid", _ORIGIN + "/"])
def test_issuer_rejects_non_exact_origin(client: TestClient, origin: str) -> None:
    response = client.post(
        "/api/auth/browser-csrf", json={"thread_id": "th_one"}, headers={**_HEADERS, "Origin": origin}
    )
    assert response.status_code == 403


def test_expired_and_forged_proofs_are_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    issued = time.time() - 901
    with monkeypatch.context() as clock:
        clock.setattr("itsdangerous.timed.time.time", lambda: issued)
        client.post("/session/session-1")
        token = _token(client)
    for value in (token, token + "tampered"):
        assert (
            client.post(
                "/api/v1/staff/threads/th_one/messages", headers={**_HEADERS, "X-CSRF-Token": value}
            ).status_code
            == 403
        )


def test_panel_creation_uses_new_thread_scope(client: TestClient) -> None:
    token = _token(client, "_new")
    assert client.post("/api/v1/staff/panels", headers={**_HEADERS, "X-CSRF-Token": token}).status_code == 200


def test_local_requests_keep_existing_csrf_contract(client: TestClient) -> None:
    assert client.post("/api/v1/staff/threads/th_one/messages", headers={"Host": "localhost"}).status_code == 200


def test_issuer_requires_authenticated_session_and_valid_scope(client: TestClient) -> None:
    client.cookies.clear()
    assert client.post("/api/auth/browser-csrf", json={"thread_id": "th_one"}, headers=_HEADERS).status_code == 403


def test_production_perimeter_rejects_before_handler(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from middleware import auth_perimeter_check
    from starlette.middleware.base import BaseHTTPMiddleware

    owner = Principal(id="owner", type="human", name="Owner")
    monkeypatch.setattr("identity.resolve_perimeter_principal", lambda _: owner)
    app = FastAPI()
    app.add_middleware(BaseHTTPMiddleware, dispatch=auth_perimeter_check)
    app.add_middleware(SessionMiddleware, secret_key="test-signing-key")  # pragma: allowlist secret
    calls = []

    @app.post("/api/v1/staff/threads/th_one/messages")
    def mutate() -> dict:
        calls.append("executed")
        return {}

    browser = TestClient(app, base_url=_ORIGIN)
    browser.cookies.update(client.cookies)
    assert browser.post("/api/v1/staff/threads/th_one/messages", headers=_HEADERS).status_code == 403
    assert not calls
    headers = {**_HEADERS, "X-CSRF-Token": _token(client)}
    assert browser.post("/api/v1/staff/threads/th_one/messages", headers=headers).status_code == 200
    assert calls == ["executed"]


def test_denial_audit_keeps_request_correlation(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    from request_context import RequestIdLogFilter

    correlation = RequestIdLogFilter()
    caplog.handler.addFilter(correlation)
    try:
        with caplog.at_level(logging.WARNING):
            response = client.post("/api/v1/staff/threads/th_one/messages", headers=_HEADERS)
        record = next(record for record in caplog.records if getattr(record, "reason", "") == "browser_csrf_denied")
        assert record.request_id == response.headers["X-Request-ID"]
        assert record.cf_ray == "test-edge" and record.source_ip
    finally:
        caplog.handler.removeFilter(correlation)
