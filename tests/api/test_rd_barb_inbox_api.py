"""GET /api/v1/staff/rd-barb/inbox: Dieter's dashboard copy of questions and alerts (RD #3075)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from routers import staff_rd_barb
from staff.rd_barb_inbox import DashboardInbox, default_inbox_path

_XHR = {"X-Requested-With": "XMLHttpRequest"}
URL = "/api/v1/staff/rd-barb/inbox"
ALERT = {"kind": "alert", "summary": "x", "urgency": "high", "dedupe_key": "a-1", "recommended_action": "Restart it."}


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


def test_lists_unacknowledged_items_newest_first(app: FastAPI) -> None:
    client = _as(app, ["fleet.read"])
    assert client.get(URL, headers=_XHR).json() == {"unacknowledged": 0, "items": []}
    store = DashboardInbox(default_inbox_path())
    store.add({**ALERT, "dedupe_key": "a-1", "summary": "first"})
    store.add({**ALERT, "dedupe_key": "a-2", "summary": "second"})
    store.acknowledge(store.unacknowledged()[1]["id"])
    body = client.get(URL, headers=_XHR).json()
    assert body["unacknowledged"] == 1 and [i["summary"] for i in body["items"]] == ["second"]
