"""Browser ingress cookies cannot reuse the longer-lived local session plane."""

import time
from http.cookies import SimpleCookie

import pytest
from browser_sessions import BrowserSessionMiddleware
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_PUBLIC_ORIGIN", "https://desk.example.ts.net")
    monkeypatch.setenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN", "https://ops.example.invalid")
    app = FastAPI()
    app.add_middleware(
        BrowserSessionMiddleware,
        secret_key="test-only-session-key",  # pragma: allowlist secret
        session_cookie="dashboard_session",
        max_age=86400,
        same_site="strict",
        https_only=False,
    )

    @app.post("/login")
    def login(request: Request) -> dict[str, bool]:
        request.session["principal_id"] = "owner"
        return {"ok": True}

    @app.get("/session")
    def session(request: Request) -> dict:
        return dict(request.session)

    @app.post("/logout")
    def logout(request: Request) -> dict[str, bool]:
        request.session.clear()
        return {"ok": True}

    return TestClient(app, base_url="https://localhost")


def _cookie(response, name: str):
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    assert list(cookies) == [name]
    return cookies[name]


@pytest.mark.parametrize("headers", [{"CF-Ray": "edge"}, {"Host": "ops.example.invalid"}])
def test_external_cookie_has_host_prefix_and_short_secure_policy(client: TestClient, headers: dict) -> None:
    cookie = _cookie(client.post("/login", headers=headers), "__Host-rd_session")
    assert cookie["secure"] and cookie["httponly"]
    assert cookie["samesite"] == "lax" and cookie["path"] == "/"
    assert cookie["max-age"] == "900" and not cookie["domain"]


def test_local_cookie_policy_remains_compatible(client: TestClient) -> None:
    cookie = _cookie(client.post("/login"), "dashboard_session")
    assert cookie["max-age"] == "86400" and cookie["samesite"] == "strict"
    assert not cookie["secure"]


def test_external_requests_never_fall_back_to_local_cookie(client: TestClient) -> None:
    client.post("/login")
    assert client.get("/session").json() == {"principal_id": "owner"}
    assert client.get("/session", headers={"CF-Ray": "edge"}).json() == {}


def test_local_requests_never_accept_external_cookie(client: TestClient) -> None:
    client.post("/login", headers={"CF-Ray": "edge"})
    assert client.get("/session", headers={"CF-Ray": "edge"}).json() == {"principal_id": "owner"}
    assert client.get("/session").json() == {}


def test_renaming_local_cookie_cannot_cross_signature_domains(client: TestClient) -> None:
    value = _cookie(client.post("/login"), "dashboard_session").value
    client.cookies.clear()
    response = client.get("/session", headers={"CF-Ray": "edge", "Cookie": f"__Host-rd_session={value}"})
    assert response.json() == {}


def test_external_logout_clears_only_external_cookie(client: TestClient) -> None:
    client.post("/login", headers={"CF-Ray": "edge"})
    cookie = _cookie(client.post("/logout", headers={"CF-Ray": "edge"}), "__Host-rd_session")
    assert cookie.value == "null" and "1970" in cookie["expires"]
    assert cookie["secure"] and cookie["httponly"]


def test_browser_cookie_expires_before_compatible_local_cookie(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    issued = time.time() - 901
    with monkeypatch.context() as clock:
        clock.setattr("itsdangerous.timed.time.time", lambda: issued)
        client.post("/login")
        client.post("/login", headers={"CF-Ray": "edge"})
    assert client.get("/session", headers={"CF-Ray": "edge"}).json() == {}
    assert client.get("/session").json() == {"principal_id": "owner"}


def test_tailnet_origin_does_not_switch_local_cookie_plane(client: TestClient) -> None:
    client.post("/login")
    assert client.get("/session", headers={"Origin": "http://desk.example.ts.net:8321"}).json() == {
        "principal_id": "owner"
    }


def test_tailnet_oauth_host_keeps_local_cookie(client: TestClient) -> None:
    cookie = _cookie(client.post("/login", headers={"Host": "desk.example.ts.net"}), "dashboard_session")
    assert cookie["max-age"] == "86400"


def test_invalid_public_origin_does_not_break_local_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN", "https://[broken")
    app = FastAPI()
    app.add_middleware(BrowserSessionMiddleware, secret_key="test-only-key")  # pragma: allowlist secret

    @app.get("/")
    def home(request: Request) -> dict:
        return {"session": request.session}

    assert TestClient(app).get("/").json() == {"session": {}}
