"""POST /api/usage/report/weekly (USE-1, issue #1865)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers.usage_metrics import create_usage_metrics_router
from session_telemetry import SessionTelemetryStore
from usage_report import week_marker

# ─── Route ───────────────────────────────────────────────────────────────────


def _client(tmp_path: Path, *, authorised: bool = True) -> TestClient:
    app = FastAPI()
    store = SessionTelemetryStore(data_file=tmp_path / "t.json", retention_days=30)
    app.include_router(create_usage_metrics_router(telemetry_store=store))
    if authorised:
        app.dependency_overrides[require_scope("admin")] = lambda: Principal(
            id="op", type="human", name="Operator", roles=["admin"]
        )
    return TestClient(app)


def test_weekly_report_route_dry_run_renders_without_github(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/api/usage/report/weekly", json={"dry_run": True})
    assert response.status_code == 200
    data = response.json()
    assert data["dry_run"] is True
    assert data["body"].startswith(week_marker(data["week"]))


def test_weekly_report_route_validates_repository(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/api/usage/report/weekly", json={"dry_run": True, "repository": "not a repo"})
    assert response.status_code == 422


def test_weekly_report_route_requires_admin_scope(tmp_path: Path) -> None:
    response = _client(tmp_path, authorised=False).post("/api/usage/report/weekly", json={"dry_run": True})
    assert response.status_code in (401, 403)
