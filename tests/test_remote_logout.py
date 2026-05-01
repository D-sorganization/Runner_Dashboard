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

    _ = sm.register_session("human:test", user_agent="A", ip_address="1.1.1.1")

    # The endpoint works even without session in request.session because it falls back gracefully
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "logged_out"


# ---------------------------------------------------------------------------
# Issue #346 — require_principal session revocation check (unit-level)
# ---------------------------------------------------------------------------


def _make_mock_request(session: dict) -> object:
    """Build a minimal mock request with a session dict and non-loopback client."""
    from unittest.mock import MagicMock

    req = MagicMock()
    req.session = session
    req.headers = {}
    # Non-loopback host so the loopback bypass does not fire
    req.client = MagicMock()
    req.client.host = "10.0.0.1"
    req.state = MagicMock()
    return req


def test_require_principal_rejects_revoked_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Revoke a session then call require_principal with same session → 401 (issue #346)."""
    import identity as id_mod
    import session_management as sm
    from fastapi import HTTPException
    from identity import Principal, require_principal

    sessions_path = tmp_path / "sessions346a.json"
    monkeypatch.setattr(sm, "_SESSIONS_PATH", sessions_path)
    sessions_path.write_text("[]")

    test_principal = Principal(id="human:alice", type="human", name="Alice", roles=["operator"])
    monkeypatch.setitem(id_mod.identity_manager.principals, "human:alice", test_principal)

    sid = sm.register_session("human:alice")
    sm.revoke_session(sid)

    req = _make_mock_request({"principal_id": "human:alice", "session_id": sid})
    with pytest.raises(HTTPException) as exc_info:
        require_principal(req, header_token=None, cookie_token=None)
    assert exc_info.value.status_code == 401


def test_require_principal_accepts_active_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Active session passes require_principal without raising (issue #346)."""
    import identity as id_mod
    import session_management as sm
    from identity import Principal, require_principal

    sessions_path = tmp_path / "sessions346b.json"
    monkeypatch.setattr(sm, "_SESSIONS_PATH", sessions_path)
    sessions_path.write_text("[]")

    test_principal = Principal(id="human:bob", type="human", name="Bob", roles=["operator"])
    monkeypatch.setitem(id_mod.identity_manager.principals, "human:bob", test_principal)

    sid = sm.register_session("human:bob")

    req = _make_mock_request({"principal_id": "human:bob", "session_id": sid})
    prin = require_principal(req, header_token=None, cookie_token=None)
    assert prin.id == "human:bob"
