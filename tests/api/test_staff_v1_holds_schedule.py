"""The v1 holds and schedule routes serve what the legacy /api/staff routes serve.

``GET /api/v1/staff/holds`` called ``HoldsList.list()``, ``PUT`` called
``StaffScheduler.replace_holds`` and ``GET /schedule`` called
``StaffScheduler.schedule_view``; none of them exist, so all three were a 500
(``internal_error``) and the Staff page's Holds tab showed "Failed to load
holds". Found in the 2026-09-28 live sweep (#1718).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers import staff_v1
from staff import holds as holds_mod
from staff import idempotency
from staff import scheduler as scheduler_mod

OPERATOR = Principal(
    id="operator-test",
    type="human",
    name="Test",
    roles=["operator"],
    scopes=["staff.read", "staff.holds.write"],
)


class _Runner:
    machine = "TestNode"

    def roles(self) -> dict[str, Any]:
        return {}


class _Scheduler:
    """Just enough of ``StaffScheduler`` for the read and replace paths."""

    def __init__(self, holds: holds_mod.HoldsList) -> None:
        self.holds = holds
        self.runner = _Runner()
        self.running = False
        self.tick_seconds = 30

    def status(self, now: Any = None) -> list[dict[str, Any]]:  # noqa: ARG002
        return [{"role": "night-watch", "schedule": "0 22 * * *", "next_fire": None}]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    holds = holds_mod.HoldsList(tmp_path / "holds.json", roles_loader=dict)
    holds.replace([{"text": "no bulk stale-queue cancel", "applies_to": ["night-watch"]}])
    monkeypatch.setattr(scheduler_mod, "_scheduler", _Scheduler(holds))
    monkeypatch.setattr(idempotency, "_GLOBAL_IDEMPOTENCY_STORE", idempotency.IdempotencyStore(tmp_path / "idem.db"))
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(staff_v1, "record_audit", lambda **kw: audits.append(kw))
    app = FastAPI()
    app.include_router(staff_v1.router)
    app.dependency_overrides[require_scope("staff.read")] = lambda: OPERATOR
    app.dependency_overrides[require_scope("staff.holds.write")] = lambda: OPERATOR
    with TestClient(app, raise_server_exceptions=False) as c:
        c.audits = audits  # type: ignore[attr-defined]
        yield c


@pytest.mark.unit
def test_v1_get_holds_lists_the_holds_file(client: TestClient) -> None:
    res = client.get("/api/v1/staff/holds")

    assert res.status_code == 200, res.text
    assert [h["text"] for h in res.json()["holds"]] == ["no bulk stale-queue cancel"]


@pytest.mark.unit
def test_v1_put_holds_replaces_the_list_and_audits(client: TestClient) -> None:
    body = {"holds": [{"text": "no Maxwell starts", "applies_to": ["*"]}]}

    res = client.put("/api/v1/staff/holds", json=body, headers={"Idempotency-Key": "holds-1"})

    assert res.status_code == 200, res.text
    assert [h["text"] for h in res.json()["holds"]] == ["no Maxwell starts"]
    assert [h["text"] for h in client.get("/api/v1/staff/holds").json()["holds"]] == ["no Maxwell starts"]
    assert client.audits[-1]["action"] == "hold_set"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_v1_put_holds_rejects_duplicate_ids_with_422(client: TestClient) -> None:
    dup = {"holds": [{"id": "d", "text": "a"}, {"id": "d", "text": "b"}]}

    res = client.put("/api/v1/staff/holds", json=dup, headers={"Idempotency-Key": "holds-dup"})

    assert res.status_code == 422, res.text


@pytest.mark.unit
def test_v1_schedule_reports_roles(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_SCHEDULER_ENABLED", "0")

    res = client.get("/api/v1/staff/schedule")

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["machine"] == "TestNode" and body["enabled"] is False and body["running"] is False
    assert [r["role"] for r in body["roles"]] == ["night-watch"]
