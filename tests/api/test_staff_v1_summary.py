"""GET /api/v1/staff/summary serves the same one-call brief as /api/staff/summary.

The v1 route called ``StaffScheduler.schedule_view``, which does not exist, so
every v1 summary was a 500 (``internal_error``) while the legacy route worked.
Found when Barb's live dispatch test asked a run for "machines online + in
flight" (2026-09-27).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers import staff_v1
from staff import summary_view

READER = Principal(id="agent-test", type="agent", name="Test", roles=["agent"], scopes=["staff.read"])

_BRIEF: dict[str, Any] = {
    "generated_at": "2026-09-27T00:00:00Z",
    "hub": "DeskComputer",
    "machines_online": ["ControlTower", "DeskComputer", "OGLaptop"],
    "machines_offline": [],
    "in_flight": [{"id": "run-1", "role": "night-watch"}],
    "recent_24h": {"succeeded": 3},
    "holds": [{"name": "C3"}],
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    async def brief() -> dict[str, Any]:
        return dict(_BRIEF)

    monkeypatch.setattr(summary_view, "build_staff_summary", brief)
    app = FastAPI()
    app.include_router(staff_v1.router)
    app.dependency_overrides[require_scope("staff.read")] = lambda: READER
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.unit
def test_v1_summary_returns_the_staff_brief(client: TestClient) -> None:
    res = client.get("/api/v1/staff/summary")

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["hub"] == "DeskComputer"
    assert body["machines_online"] == ["ControlTower", "DeskComputer", "OGLaptop"]
    assert [r["id"] for r in body["in_flight"]] == ["run-1"]
    assert body["holds"] == [{"name": "C3"}]
