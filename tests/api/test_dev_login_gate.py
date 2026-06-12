"""Dev-login perimeter hardening tests (issue #921).

The /api/auth/dev-login endpoint mints an admin session for the first human
principal. Before #921 it was reachable by any network peer whenever OAuth was
unconfigured (the out-of-the-box default), combined with a hardcoded 0.0.0.0
bind — i.e. unauthenticated remote admin.

Acceptance criteria verified here:
- Non-loopback request to GET /api/auth/dev-login returns 404 (even with the
  opt-in flag set).
- Loopback request WITHOUT DASHBOARD_DEV_LOGIN=1 is rejected (404).
- Loopback request WITH DASHBOARD_DEV_LOGIN=1 still works for local dev.
- The server __main__ bind honors dashboard_config.HOST rather than a hardcoded
  0.0.0.0.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

_BACKEND_DIR = Path(__file__).parent.parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> None:
    """Clear the shared per-IP auth rate-limit store (issue #320) between tests."""
    import middleware

    middleware._auth_rate_store.clear()


def _make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Minimal app wired to the auth router with a temp identity store."""
    import session_management as sm
    from identity import IdentityManager
    from routers import auth as auth_module

    sessions_path = tmp_path / "sessions.json"
    sessions_path.write_text("[]")
    monkeypatch.setattr(sm, "_SESSIONS_PATH", sessions_path)

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    mgr = IdentityManager(config_dir=config_dir)
    monkeypatch.setattr(auth_module, "identity_manager", mgr)
    monkeypatch.setattr(auth_module, "GITHUB_CLIENT_ID", "")

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")  # pragma: allowlist secret
    app.include_router(auth_module.router)
    return app


def _client(app: FastAPI, host: str) -> TestClient:
    """TestClient whose transport peer address is ``host``."""
    return TestClient(app, follow_redirects=False, client=(host, 12345))


# ── Non-loopback is always rejected, even with the opt-in flag ────────────────


def test_non_loopback_dev_login_returns_404_even_with_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_DEV_LOGIN", "1")
    app = _make_app(tmp_path, monkeypatch)
    resp = _client(app, "192.168.1.50").get("/api/auth/dev-login")
    assert resp.status_code == 404


def test_non_loopback_dev_login_returns_404_without_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_DEV_LOGIN", raising=False)
    app = _make_app(tmp_path, monkeypatch)
    resp = _client(app, "10.0.0.7").get("/api/auth/dev-login")
    assert resp.status_code == 404


# ── Loopback without the opt-in flag is rejected ──────────────────────────────


def test_loopback_dev_login_without_flag_returns_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_DEV_LOGIN", raising=False)
    app = _make_app(tmp_path, monkeypatch)
    resp = _client(app, "127.0.0.1").get("/api/auth/dev-login")
    assert resp.status_code == 404


# ── Loopback with the opt-in flag still works for local development ───────────


def test_loopback_dev_login_with_flag_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_DEV_LOGIN", "1")
    app = _make_app(tmp_path, monkeypatch)
    resp = _client(app, "127.0.0.1").get("/api/auth/dev-login")
    assert resp.status_code in (200, 302, 307)
    # A session principal cookie should now be set.
    assert resp.cookies or "set-cookie" in {k.lower() for k in resp.headers}


def test_loopback_ipv6_dev_login_with_flag_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_DEV_LOGIN", "1")
    app = _make_app(tmp_path, monkeypatch)
    resp = _client(app, "::1").get("/api/auth/dev-login")
    assert resp.status_code in (200, 302, 307)


# ── Server bind honors dashboard_config.HOST (issue #921) ─────────────────────


def test_server_main_binds_resolved_host_not_hardcoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`python backend/server.py` must pass dashboard_config.HOST to uvicorn.run,
    and DASHBOARD_HOST must be honored."""
    import importlib

    import dashboard_config

    monkeypatch.setenv("DASHBOARD_HOST", "127.0.0.1")
    importlib.reload(dashboard_config)
    assert dashboard_config.HOST == "127.0.0.1"

    src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    # The uvicorn.run call must reference the resolved host, not a literal bind-all.
    assert "host=dashboard_config.HOST" in src
    assert 'host="0.0.0.0"' not in src

    monkeypatch.delenv("DASHBOARD_HOST", raising=False)
    importlib.reload(dashboard_config)
    assert dashboard_config.HOST == "0.0.0.0"  # default preserved
