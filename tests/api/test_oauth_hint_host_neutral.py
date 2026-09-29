"""Tests for host-neutral and Tailscale-aware GitHub OAuth 503 hints (issue #1764).

Acceptance criteria:
- The 503 hint no longer names a fixed host (e.g. 'OGLaptop'); it either names the
  serving node or stays host-neutral.
- When OAuth is not ready and Tailscale identity sign-in is disabled, the hint
  mentions DASHBOARD_TAILSCALE_AUTH as the tailnet alternative.
- Asserts the 503 hint contains no hard-coded host name.
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
    """Clear shared rate limit store between tests."""
    import middleware

    middleware._auth_rate_store.clear()


def _make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
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

    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")  # pragma: allowlist secret
    app.include_router(auth_module.router)
    return app


def test_oauth_503_hint_contains_no_hardcoded_oglaptop_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 503 hint must not contain the hardcoded host 'OGLaptop'."""
    monkeypatch.delenv("DASHBOARD_TAILSCALE_AUTH", raising=False)
    app = _make_app(tmp_path, monkeypatch)
    client = TestClient(app)

    resp = client.get("/api/auth/github")
    assert resp.status_code == 503
    data = resp.json()
    hint = data["detail"]["hint"]

    # Must NOT name the hard-coded host OGLaptop
    assert "OGLaptop" not in hint
    assert "oglaptop" not in hint.lower()


def test_oauth_503_hint_mentions_tailscale_auth_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When OAuth is blocked and DASHBOARD_TAILSCALE_AUTH is unset, hint mentions DASHBOARD_TAILSCALE_AUTH."""
    monkeypatch.delenv("DASHBOARD_TAILSCALE_AUTH", raising=False)
    app = _make_app(tmp_path, monkeypatch)
    client = TestClient(app)

    resp = client.get("/api/auth/github")
    assert resp.status_code == 503
    hint = resp.json()["detail"]["hint"]

    assert "DASHBOARD_TAILSCALE_AUTH" in hint


def test_oauth_503_hint_when_tailscale_auth_is_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When DASHBOARD_TAILSCALE_AUTH=1, hint remains host-neutral
    without suggesting enabling DASHBOARD_TAILSCALE_AUTH.
    """
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    app = _make_app(tmp_path, monkeypatch)
    client = TestClient(app)

    resp = client.get("/api/auth/github")
    assert resp.status_code == 503
    hint = resp.json()["detail"]["hint"]

    assert "OGLaptop" not in hint
    assert "enable DASHBOARD_TAILSCALE_AUTH" not in hint
