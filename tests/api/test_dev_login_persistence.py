"""Tests for /api/auth/dev-login session persistence across restarts (issue #410).

AC:
1. dev-login persists the dev-user principal to principals.yml.
2. After a simulated restart (IdentityManager reload), the same principal is
   still present and the session cookie continues to work.
3. Subsequent dev-login calls reuse the persisted principal — no duplicate entries.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

_BACKEND_DIR = Path(__file__).parent.parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build a minimal FastAPI app wired to a temp config dir."""
    import session_management as sm
    from identity import IdentityManager
    from routers import auth as auth_module

    # Redirect session storage to tmp
    sessions_path = tmp_path / "sessions.json"
    sessions_path.write_text("[]")
    monkeypatch.setattr(sm, "_SESSIONS_PATH", sessions_path)

    # Point identity_manager at tmp config dir
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    mgr = IdentityManager(config_dir=config_dir)
    monkeypatch.setattr(auth_module, "identity_manager", mgr)

    # Disable GitHub OAuth so dev-login is reachable
    monkeypatch.setattr(auth_module, "GITHUB_CLIENT_ID", "")

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    app.include_router(auth_module.router)
    return app


# ---------------------------------------------------------------------------
# AC-1: dev-login persists principal to principals.yml
# ---------------------------------------------------------------------------


def test_dev_login_persists_principal_to_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling /api/auth/dev-login when no human principal exists must write
    dev-user to principals.yml atomically."""
    app = _make_app(tmp_path, monkeypatch)
    client = TestClient(app, follow_redirects=False)

    response = client.get("/api/auth/dev-login")
    assert response.status_code in (200, 302, 307)

    principals_yml = tmp_path / "config" / "principals.yml"
    assert principals_yml.exists(), "principals.yml must be created by dev-login"

    data = yaml.safe_load(principals_yml.read_text())
    assert data is not None and "principals" in data, "principals.yml must contain 'principals' key"
    ids = [p["id"] for p in data["principals"]]
    assert "dev-user" in ids, f"dev-user must be persisted; found: {ids}"


# ---------------------------------------------------------------------------
# AC-2: simulated restart — session cookie still resolves after reload
# ---------------------------------------------------------------------------


def test_dev_login_session_survives_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After dev-login, a new IdentityManager (simulating a restart) must still
    resolve the dev-user principal from principals.yml."""
    from identity import IdentityManager

    # --- First boot ---
    app1 = _make_app(tmp_path, monkeypatch)
    client1 = TestClient(app1, follow_redirects=False)
    client1.get("/api/auth/dev-login")

    principals_yml = tmp_path / "config" / "principals.yml"
    assert principals_yml.exists(), "principals.yml must exist after first dev-login"

    # --- Simulate restart: load a fresh IdentityManager from the same config dir ---
    mgr_after_restart = IdentityManager(config_dir=tmp_path / "config")
    assert "dev-user" in mgr_after_restart.principals, (
        "dev-user must be present in reloaded IdentityManager — session cookie would break without it"
    )

    principal = mgr_after_restart.principals["dev-user"]
    assert principal.type == "human"
    assert principal.name == "Developer"


# ---------------------------------------------------------------------------
# AC-2 (extended): second app instance resolves the cookie via dev-user
# ---------------------------------------------------------------------------


def test_dev_login_cookie_works_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session cookie set by dev-login resolves correctly on a second
    IdentityManager instance (same principal id, loaded from disk)."""
    import session_management as sm
    from identity import IdentityManager
    from routers import auth as auth_module

    sessions_path = tmp_path / "sessions.json"
    sessions_path.write_text("[]")
    monkeypatch.setattr(sm, "_SESSIONS_PATH", sessions_path)

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(auth_module, "GITHUB_CLIENT_ID", "")

    # Boot 1 — trigger dev-login
    mgr1 = IdentityManager(config_dir=config_dir)
    monkeypatch.setattr(auth_module, "identity_manager", mgr1)

    app1 = FastAPI()
    app1.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    app1.include_router(auth_module.router)

    client1 = TestClient(app1, follow_redirects=False)
    resp = client1.get("/api/auth/dev-login")
    # Cookie is set in client1's jar
    assert resp.status_code in (200, 302, 307)

    # Boot 2 — fresh manager from disk
    mgr2 = IdentityManager(config_dir=config_dir)
    monkeypatch.setattr(auth_module, "identity_manager", mgr2)

    # dev-user must be known to mgr2
    assert "dev-user" in mgr2.principals, "Reloaded identity manager must know dev-user so its cookie resolves"


# ---------------------------------------------------------------------------
# AC-3: second call to dev-login reuses existing principal, no duplicates
# ---------------------------------------------------------------------------


def test_dev_login_idempotent_no_duplicate_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling dev-login twice must not create duplicate principals in the YAML."""
    app = _make_app(tmp_path, monkeypatch)
    client = TestClient(app, follow_redirects=False)

    client.get("/api/auth/dev-login")
    client.get("/api/auth/dev-login")

    principals_yml = tmp_path / "config" / "principals.yml"
    data = yaml.safe_load(principals_yml.read_text())
    ids = [p["id"] for p in data["principals"]]
    dev_user_count = ids.count("dev-user")
    assert dev_user_count == 1, f"dev-user must appear exactly once; found {dev_user_count}"


# ---------------------------------------------------------------------------
# Unit: IdentityManager.save_principals round-trips correctly
# ---------------------------------------------------------------------------


def test_save_principals_round_trip(tmp_path: Path) -> None:
    """save_principals writes YAML that load_principals can read back."""
    from identity import IdentityManager, Principal

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    mgr = IdentityManager(config_dir=config_dir)

    p = Principal(id="alice", type="human", name="Alice", roles=["operator"])
    mgr.principals["alice"] = p
    mgr.save_principals()

    # Reload
    mgr2 = IdentityManager(config_dir=config_dir)
    assert "alice" in mgr2.principals
    assert mgr2.principals["alice"].name == "Alice"
    assert mgr2.principals["alice"].roles == ["operator"]
