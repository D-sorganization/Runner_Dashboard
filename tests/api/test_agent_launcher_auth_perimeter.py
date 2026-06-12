"""Auth-perimeter regression tests for the agent-launcher router (issue #920).

The agent-launcher control surface spawns code-executing agent processes and
writes attacker-controllable JSON into the operator's home directory. Before
this fix every route was unauthenticated, so any LAN/Tailscale peer reaching
the dashboard port could start/stop/configure agents (RCE-adjacent).

These tests assert:
- Unauthenticated calls to every route return 401.
- An authenticated principal lacking ``system.control`` gets 403 on mutating
  routes (config PUT, start, stop, run-once).
- A properly scoped principal still passes the auth gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = str(Path(__file__).resolve().parents[2] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import agent_launcher_router as alr  # noqa: E402

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason="requires Python 3.11+")


# Routes that mutate state / spawn processes — require system.control.
_MUTATING = [
    ("put", "/api/agent-launcher/config", {"agents": {}}),
    ("post", "/api/agent-launcher/start", None),
    ("post", "/api/agent-launcher/stop", None),
    ("post", "/api/agent-launcher/run-once", {"agent": "x"}),
]

# Read routes — require an authenticated principal (any role).
_READ = [
    ("get", "/api/agent-launcher/status"),
    ("get", "/api/agent-launcher/config"),
    ("get", "/api/agent-launcher/repos"),
]


@pytest.fixture
def app() -> FastAPI:
    from starlette.middleware.sessions import SessionMiddleware  # noqa: PLC0415

    app = FastAPI()
    # Production mounts SessionMiddleware; require_principal consults
    # request.session, so the test app must mirror that to reach the 401 path
    # rather than tripping Starlette's "SessionMiddleware must be installed".
    app.add_middleware(SessionMiddleware, secret_key="test-perimeter")  # noqa: S106  # pragma: allowlist secret
    app.include_router(alr.router)
    return app


def _call(client: TestClient, method: str, url: str, json_body=None):
    return getattr(client, method)(url, json=json_body) if json_body is not None else getattr(client, method)(url)


@pytest.mark.parametrize(("method", "url", "body"), _MUTATING)
def test_mutating_routes_reject_unauthenticated(app: FastAPI, method: str, url: str, body) -> None:
    client = TestClient(app)
    resp = _call(client, method, url, body)
    assert resp.status_code == 401, f"{method.upper()} {url} should require auth, got {resp.status_code}"


@pytest.mark.parametrize(("method", "url"), _READ)
def test_read_routes_reject_unauthenticated(app: FastAPI, method: str, url: str) -> None:
    client = TestClient(app)
    resp = _call(client, method, url)
    assert resp.status_code == 401, f"{method.upper()} {url} should require auth, got {resp.status_code}"


@pytest.mark.parametrize(("method", "url", "body"), _MUTATING)
def test_mutating_routes_reject_insufficient_scope(app: FastAPI, method: str, url: str, body) -> None:
    """A viewer principal (no system.control scope) gets 403 on mutating routes."""
    from identity import Principal, require_principal  # noqa: PLC0415

    app.dependency_overrides[require_principal] = lambda: Principal(
        id="viewer", type="bot", name="Viewer", roles=["viewer"]
    )
    try:
        client = TestClient(app)
        resp = _call(client, method, url, body)
        assert resp.status_code == 403, f"{method.upper()} {url} should require system.control, got {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


def test_status_allows_authenticated_principal(app: FastAPI, monkeypatch, tmp_path) -> None:
    """A read route succeeds for any authenticated principal."""
    from identity import Principal, require_principal  # noqa: PLC0415

    monkeypatch.setattr(alr, "_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(alr, "_is_pid_alive", lambda pid: False)
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="viewer", type="bot", name="Viewer", roles=["viewer"]
    )
    try:
        client = TestClient(app)
        resp = client.get("/api/agent-launcher/status")
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()
