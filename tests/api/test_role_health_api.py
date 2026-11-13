"""API tests for GET /api/v1/staff/roles/health and POST /api/v1/staff/roles/{role}/heartbeat (#3295 S3, #3296)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from routers import staff_rd_barb
from staff.role_health import HEARTBEAT_INTERVAL_S

_XHR = {"X-Requested-With": "XMLHttpRequest"}
HEALTH_URL = "/api/v1/staff/roles/health"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    application = FastAPI()
    application.include_router(staff_rd_barb.router)
    return application


def _as(app: FastAPI, scopes: list[str]) -> TestClient:
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="grok-barb", type="bot", name="Grok Barb", roles=[], scopes=scopes
    )
    return TestClient(app)


def test_roles_health_requires_fleet_read_and_fails_unknown_to_degraded(app: FastAPI) -> None:
    assert TestClient(app).get(HEALTH_URL, headers=_XHR).status_code in (401, 403)
    assert _as(app, []).get(HEALTH_URL, headers=_XHR).status_code == 403
    body = _as(app, ["fleet.read"]).get(HEALTH_URL, params={"roles": "bob"}, headers=_XHR).json()
    assert body["status"] == "degraded" and body["roles"]["bob"]["reasons"] == ["unknown_health"]


def test_heartbeat_and_aggregate_health_transitions(app: FastAPI) -> None:
    client = _as(app, ["fleet.read", "staff.dispatch"])
    t0 = 10_000.0
    assert (
        client.post("/api/v1/staff/roles/bob/heartbeat", json={"epoch": 2, "now": t0}, headers=_XHR).json()["state"]
        == "healthy"
    )
    assert client.get(HEALTH_URL, params={"roles": "bob", "now": t0 + 60.0}, headers=_XHR).json()["status"] == "healthy"

    t_late = t0 + 2 * HEARTBEAT_INTERVAL_S
    assert client.get(HEALTH_URL, params={"roles": "bob", "now": t_late}, headers=_XHR).json()["status"] == "degraded"
    unhealthy = client.get(HEALTH_URL, params={"roles": "bob", "now": t_late + 30.0}, headers=_XHR).json()
    assert unhealthy["status"] == "unhealthy" and unhealthy["roles"]["bob"]["transition"] == "role_unhealthy"
