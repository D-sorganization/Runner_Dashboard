"""Tests for POST /api/auth/refresh (issue #1735).

Mirrors GET /api/auth/me's auth resolution (``Depends(require_principal)``):
an authenticated session principal gets 200 with ``{"ok": True, "principal": id}``,
an unauthenticated caller gets 401.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _make_app():
    from fastapi import FastAPI
    from routers import auth as auth_module
    from starlette.middleware.sessions import SessionMiddleware

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")  # pragma: allowlist secret
    app.include_router(auth_module.router)
    return app


def test_refresh_returns_ok_and_principal_for_authenticated_caller() -> None:
    from fastapi.testclient import TestClient
    from identity import Principal, require_principal

    app = _make_app()
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="test-user", type="human", name="Test User", roles=["viewer"]
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.post("/api/auth/refresh")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "principal": "test-user"}


def test_refresh_requires_auth() -> None:
    from fastapi.testclient import TestClient

    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 401
