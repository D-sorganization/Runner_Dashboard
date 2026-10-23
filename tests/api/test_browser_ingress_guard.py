from __future__ import annotations

import asyncio
import secrets
import threading
from types import SimpleNamespace

import pytest
from browser_ingress_guard import BrowserIngressMiddleware
from browser_sessions import BrowserSessionMiddleware
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from middleware import add_security_headers


class AccessValidator:
    def __init__(self, *, valid: bool = True) -> None:
        self.valid = valid
        self.calls = 0
        self.thread_ids: list[int] = []

    def validate(self, token: str) -> dict[str, str]:
        self.calls += 1
        self.thread_ids.append(threading.get_ident())
        if not self.valid or token != "signed-access":
            raise ValueError("invalid")
        return {"sub": "owner@example.invalid"}


class RateLimiter:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.keys: list[tuple[str, str, str]] = []

    def acquire(self, owner: str, thread: str, action: str) -> tuple[bool, float]:
        self.keys.append((owner, thread, action))
        return self.allowed, 60.0


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch):
    origin = "https://ops.example.invalid"
    monkeypatch.setenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("DASHBOARD_BROWSER_INGRESS_ENABLED", "true")
    validator = AccessValidator()
    limiter = RateLimiter()
    app = FastAPI()
    app.state.browser_access_validator = validator

    @app.get("/")
    async def shell(request: Request):
        if request.headers.get("x-test-seed"):
            request.session.update(
                principal_id="principal-owner",
                session_id="session-1",
                github_user_id=request.headers.get("x-test-owner", "12345"),
            )
        return {"ok": True}

    @app.get("/api/queue")
    async def queue():
        return {"queue": []}

    @app.post("/api/auth/logout")
    async def logout():
        return {"logout": True}

    @app.post("/api/auth/refresh")
    async def refresh():
        return {"refresh": True}

    @app.post("/api/v1/staff/threads/th_one/messages")
    async def post_message():
        return {"sent": True}

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        return await add_security_headers(request, call_next)

    app.add_middleware(
        BrowserIngressMiddleware,
        validator=validator,
        enabled=True,
        owner_github_id="12345",
        principal_lookup=lambda principal_id: SimpleNamespace(id=principal_id, type="human"),
        session_is_active=lambda session_id: session_id == "session-1",
        rate_limiter=limiter,
    )
    app.add_middleware(
        BrowserSessionMiddleware,
        secret_key=secrets.token_urlsafe(32),
        https_only=True,
    )
    client = TestClient(app, base_url=origin, raise_server_exceptions=False)
    return client, app, validator, limiter


def edge_headers(**extra: str) -> dict[str, str]:
    return {"CF-Ray": "test-edge", "CF-Access-Jwt-Assertion": "signed-access", **extra}


def seed_owner(browser) -> None:
    client = browser[0]
    response = client.get("/", headers=edge_headers(**{"X-Test-Seed": "owner"}))
    assert response.status_code == 200
    assert "__Host-rd_session" in client.cookies


def test_disabled_guard_fails_closed_without_jwks_or_handler(browser, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, validator, _ = browser
    monkeypatch.setenv("DASHBOARD_BROWSER_INGRESS_ENABLED", "false")
    response = client.get("/", headers=edge_headers())
    assert response.status_code == 403
    assert response.json() == {"error": "Request not permitted"}
    assert validator.calls == 0


def test_access_validated_off_event_loop_for_public_shell(browser) -> None:
    client, _, validator, _ = browser
    response = client.get("/", headers=edge_headers())
    assert response.status_code == 200
    assert validator.calls == 1
    assert validator.thread_ids[0] != threading.get_ident()


def test_auth_logout_requires_exact_origin_and_xhr(browser) -> None:
    client, _, _, _ = browser
    seed_owner(browser)
    response = client.post("/api/auth/logout", headers=edge_headers())
    assert response.status_code == 403
    response = client.post(
        "/api/auth/logout",
        headers=edge_headers(Origin="https://ops.example.invalid", **{"X-Requested-With": "XMLHttpRequest"}),
    )
    assert response.status_code == 200


def test_bearer_nonowner_and_unknown_routes_fail_closed(browser) -> None:
    client, _, validator, _ = browser
    denied = client.get("/api/queue", headers=edge_headers(Authorization="Bearer machine-secret"))
    assert denied.status_code == 403
    assert validator.calls == 0
    unknown = client.get("/api/coordination/messages", headers=edge_headers())
    assert unknown.status_code == 403
    assert validator.calls == 0
    seed_owner(browser)
    mismatch = client.get("/", headers=edge_headers(**{"X-Test-Seed": "owner", "X-Test-Owner": "999"}))
    assert mismatch.status_code == 200
    mismatch = client.get("/api/queue", headers=edge_headers())
    assert mismatch.status_code == 403


def test_thread_write_requires_csrf_and_rate_limit(browser) -> None:
    client, app, _, limiter = browser
    seed_owner(browser)
    headers = edge_headers(Origin="https://ops.example.invalid", **{"X-Requested-With": "XMLHttpRequest"})
    response = client.post("/api/v1/staff/threads/th_one/messages", headers=headers)
    assert response.status_code == 403
    assert limiter.keys == []
    app.state.browser_csrf_check = lambda request, principal: None
    limiter.allowed = False
    response = client.post("/api/v1/staff/threads/th_one/messages", headers=headers)
    assert response.status_code == 429
    assert limiter.keys == [("12345", "th_one", "messages")]


@pytest.mark.asyncio
async def test_sse_frames_pass_incrementally_through_the_guard(browser) -> None:
    _, _, validator, limiter = browser
    first_sent = asyncio.Event()
    release = asyncio.Event()
    sent: list[dict] = []

    async def streaming_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"event: one\n\n", "more_body": True})
        first_sent.set()
        await release.wait()
        await send({"type": "http.response.body", "body": b"event: two\n\n", "more_body": False})

    guard = BrowserIngressMiddleware(
        streaming_app,
        validator=validator,
        enabled=True,
        owner_github_id="12345",
        principal_lookup=lambda principal_id: SimpleNamespace(id=principal_id, type="human"),
        session_is_active=lambda session_id: session_id == "session-1",
        rate_limiter=limiter,
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/api/v1/staff/threads/th_one/stream",
        "raw_path": b"/api/v1/staff/threads/th_one/stream",
        "query_string": b"",
        "headers": [
            (b"host", b"ops.example.invalid"),
            (b"cf-ray", b"test-edge"),
            (b"cf-access-jwt-assertion", b"signed-access"),
        ],
        "client": ("192.0.2.10", 1234),
        "server": ("ops.example.invalid", 443),
        "state": {},
        "session": {
            "principal_id": "principal-owner",
            "session_id": "session-1",
            "github_user_id": "12345",
        },
        "app": SimpleNamespace(state=SimpleNamespace()),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(guard(scope, receive, send))
    await asyncio.wait_for(first_sent.wait(), timeout=1)
    bodies = [item["body"] for item in sent if item["type"] == "http.response.body"]
    assert bodies == [b"event: one\n\n"]
    release.set()
    await task
    bodies = [item["body"] for item in sent if item["type"] == "http.response.body"]
    assert bodies == [b"event: one\n\n", b"event: two\n\n"]


@pytest.fixture
def real_server_browser(monkeypatch: pytest.MonkeyPatch):
    import server

    origin = "https://ops.example.invalid"
    monkeypatch.setenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("DASHBOARD_BROWSER_INGRESS_ENABLED", "true")
    server.app.state.browser_access_validator = AccessValidator()
    return TestClient(server.app, base_url=origin, raise_server_exceptions=False)


def test_real_server_guard_rejects_protected_api_without_owner_cookie(real_server_browser) -> None:
    response = real_server_browser.get("/api/queue", headers=edge_headers())
    assert response.status_code == 401
    response = real_server_browser.post(
        "/api/auth/refresh",
        headers=edge_headers(Origin="https://ops.example.invalid", **{"X-Requested-With": "XMLHttpRequest"}),
    )
    assert response.status_code == 401
    response = real_server_browser.post("/api/v1/staff/threads/th_one/messages", headers=edge_headers())
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Request not permitted"


def test_real_server_auth_handshake_requires_access_but_no_dashboard_session(real_server_browser) -> None:
    missing_access = real_server_browser.get("/api/auth/github")
    assert missing_access.status_code == 403
    access_valid = real_server_browser.get("/api/auth/github", headers=edge_headers())
    assert access_valid.status_code not in {401, 403}


def test_real_server_middleware_order_keeps_session_outermost() -> None:
    import server

    names = [middleware.cls.__name__ for middleware in server.app.user_middleware]
    auth_index = next(
        index
        for index, middleware in enumerate(server.app.user_middleware)
        if getattr(middleware.kwargs.get("dispatch"), "__name__", "") == "auth_perimeter_check"
    )
    assert names.index("BrowserSessionMiddleware") < names.index("BrowserIngressMiddleware") < auth_index
