"""GET /api/v1/staff/rd-barb/notify-status: where the front door sees a failed direct delivery (RD #3075)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from routers import staff_rd_barb
from staff.rd_barb_notify_health import NotifyHealth, default_health_path

_XHR = {"X-Requested-With": "XMLHttpRequest"}
URL = "/api/v1/staff/rd-barb/notify-status"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    application = FastAPI()
    application.include_router(staff_rd_barb.router)
    return application


def _as(app: FastAPI, scopes: list[str]) -> TestClient:
    prin = Principal(id="grok-barb", type="bot", name="Grok Barb", roles=[], scopes=scopes)
    app.dependency_overrides[require_principal] = lambda: prin
    return TestClient(app)


def test_requires_fleet_read(app: FastAPI) -> None:
    assert TestClient(app).get(URL, headers=_XHR).status_code in (401, 403)
    assert _as(app, []).get(URL, headers=_XHR).status_code == 403


def test_reports_never_attempted_then_failing(app: FastAPI) -> None:
    client = _as(app, ["fleet.read"])
    assert client.get(URL, headers=_XHR).json()["state"] == "never_attempted"
    NotifyHealth(default_health_path()).record(ok=False, channel="none", error="no_channel_configured", now=time.time())
    body = client.get(URL, headers=_XHR).json()
    assert body["state"] == "failing" and body["last_error"] == "no_channel_configured"
    assert body["degraded_after_s"] == 300.0
