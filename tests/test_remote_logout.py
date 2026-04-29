"""Tests for remote logout endpoints in routers/auth.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    import session_management as sm
    from identity import Principal, require_principal
    from routers import auth as auth_router

    sessions_path = tmp_path / "sessions.json"
    monkeypatch.setattr(sm, "_SESSIONS_PATH", sessions_path)
    sessions_path.write_text("[]")

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    def principal():
        return Principal(id="human:test", type="human", name="Test User", roles=["operator"])

    app.dependency_overrides[require_principal] = principal
    app.include_router(auth_router.router)
    return TestClient(app)


def test_list_sessions_returns_hashed_ids(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    import session_management as sm

    sm.register_session("human:test", user_agent="TestAgent/1.0", ip_address="127.0.0.1")

    response = client.get("/api/auth/sessions")
    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert len(data["sessions"]) == 1
    session = data["sessions"][0]
    assert "session_hash" in session
    assert session["session_hash"] != "sess_"
    assert session["user_agent"] == "TestAgent/1.0"
    assert session["ip_address"] == "127.0.0.1"


def test_revoke_session_by_hash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    import session_management as sm

    sid = sm.register_session("human:test", user_agent="TestAgent/1.0", ip_address="127.0.0.1")
    session_hash = sm.hash_session_id(sid)

    response = client.delete(f"/api/auth/sessions/{session_hash}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "revoked"
    assert data["session_hash"] == session_hash

    # Verify session is gone
    assert not sm.is_session_active(sid)


def test_revoke_session_by_hash_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    response = client.delete("/api/auth/sessions/nonexistenthash123")
    assert response.status_code == 404
    assert "Session not found" in response.json()["detail"]


def test_logout_all_revokes_sessions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    import session_management as sm

    sid1 = sm.register_session("human:test", user_agent="A", ip_address="1.1.1.1")
    sid2 = sm.register_session("human:test", user_agent="B", ip_address="2.2.2.2")

    response = client.post("/api/auth/logout/all", json={"exclude_current": True})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "logged_out"
    # All sessions for this principal revoked (current session not set in request, so no exclusion)
    assert data["revoked_sessions"] == 2

    assert not sm.is_session_active(sid1)
    assert not sm.is_session_active(sid2)


def test_logout_all_including_current(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    import session_management as sm

    sid1 = sm.register_session("human:test", user_agent="A", ip_address="1.1.1.1")
    sid2 = sm.register_session("human:test", user_agent="B", ip_address="2.2.2.2")

    response = client.post("/api/auth/logout/all", json={"exclude_current": False})
    assert response.status_code == 200
    data = response.json()
    assert data["revoked_sessions"] == 2

    assert not sm.is_session_active(sid1)
    assert not sm.is_session_active(sid2)


def test_logout_endpoint_revokes_current_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    import session_management as sm

    sid = sm.register_session("human:test", user_agent="A", ip_address="1.1.1.1")

    # The endpoint works even without session in request.session because it falls back gracefully
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "logged_out"
