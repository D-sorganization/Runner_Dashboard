"""Security contract for POST /_drain and POST /_undrain (WP19 #4274, F-04).

Before F-04, an unauthenticated loopback POST /_drain returned HTTP 200 and latched
``_drain_mode`` with no CSRF header. This module locks the tightened contract.
"""

from __future__ import annotations

import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import server  # noqa: E402
from identity import Principal, require_principal, require_scope  # noqa: E402

_CSRF_HEADERS = {"X-Requested-With": "XMLHttpRequest"}
_ADMIN = Principal(id="test-admin", type="bot", name="Test Admin", roles=["admin"])
_VIEWER = Principal(id="test-viewer", type="bot", name="Test Viewer", roles=["viewer"])


def _post_drain(client: TestClient):
    return client.post("/_drain", headers=_CSRF_HEADERS)


def _post_undrain(client: TestClient):
    return client.post("/_undrain", headers=_CSRF_HEADERS)


@pytest.fixture
def loopback_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(server, "_drain_mode", False)
    monkeypatch.delenv("DASHBOARD_LOOPBACK_AUTH", raising=False)
    client = TestClient(server.app, client=("127.0.0.1", 43120), raise_server_exceptions=False)
    try:
        yield client
    finally:
        client.close()
        server.app.dependency_overrides.clear()


@pytest.fixture
def admin_loopback_client(loopback_client: TestClient) -> TestClient:
    server.app.dependency_overrides[require_scope("admin")] = lambda: _ADMIN
    return loopback_client


def test_unauthenticated_loopback_drain_refused(loopback_client: TestClient) -> None:
    """F-04 characterization flip: pre-fix loopback POST /_drain returned 200 without auth."""
    assert loopback_client.post("/_drain", headers=_CSRF_HEADERS).status_code == 401
    assert server._drain_mode is False


def test_drain_missing_csrf_header_refused(admin_loopback_client: TestClient) -> None:
    response = admin_loopback_client.post("/_drain")
    assert response.status_code == 403
    assert server._drain_mode is False


def test_viewer_scope_cannot_drain(loopback_client: TestClient) -> None:
    server.app.dependency_overrides[require_principal] = lambda: _VIEWER
    response = _post_drain(loopback_client)
    assert response.status_code == 403
    assert server._drain_mode is False


def test_admin_can_drain(admin_loopback_client: TestClient) -> None:
    response = _post_drain(admin_loopback_client)
    assert response.status_code == 200
    assert response.json() == {"status": "draining"}
    assert server._drain_mode is True


def test_drain_then_undrain_restores_mutation_admission(admin_loopback_client: TestClient) -> None:
    effects: list[str] = []

    def mutate() -> dict[str, str]:
        effects.append("mutated")
        return {"status": "mutated"}

    route = APIRoute("/__test_drain_security_mutation", mutate, methods=["POST"])
    server.app.router.routes.insert(0, route)
    try:
        assert _post_drain(admin_loopback_client).status_code == 200
        blocked = admin_loopback_client.post("/__test_drain_security_mutation", headers=_CSRF_HEADERS)
        assert blocked.status_code == 503

        undrain = _post_undrain(admin_loopback_client)
        assert undrain.status_code == 200
        assert undrain.json() == {"status": "admitting"}
        assert server._drain_mode is False

        admitted = admin_loopback_client.post("/__test_drain_security_mutation", headers=_CSRF_HEADERS)
        assert admitted.status_code == 200
        assert effects == ["mutated"]
    finally:
        server.app.router.routes.remove(route)


def test_undrain_requires_admin_and_csrf(loopback_client: TestClient) -> None:
    server._drain_mode = True
    try:
        assert loopback_client.post("/_undrain", headers=_CSRF_HEADERS).status_code == 401
        server.app.dependency_overrides[require_scope("admin")] = lambda: _ADMIN
        assert loopback_client.post("/_undrain").status_code == 403
        assert _post_undrain(loopback_client).status_code == 200
        assert server._drain_mode is False
    finally:
        server._drain_mode = False
