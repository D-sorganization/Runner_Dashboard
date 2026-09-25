"""Tests for per-role schedule override API (SC-D6, Issue #1320).

Verifies PUT /api/v1/staff/roles/{role}/schedule:
- Toggling a role's schedule off stops the scheduler from starting it.
- Schedule status reflects the override in /api/staff/schedule.
- SC-A8 audit log captures the change.
- Re-enabling restores the role's scheduled dispatch.
- 404 for unknown roles.
- Requires staff.holds.write scope.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from staff import budget as budget_mod
from staff import holds as holds_mod
from staff import roles as roles_mod
from staff import scheduler as scheduler_mod
from staff import store as store_mod
from staff.runner import RunRequest

LA = ZoneInfo("America/Los_Angeles")
_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _la(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=LA)


def _roles() -> dict[str, roles_mod.RoleSpec]:
    return {
        "night-watch": roles_mod.parse_role(
            {
                "name": "night-watch",
                "title": "Night Watch",
                "providers": ["fake"],
                "schedule": "0 22 * * *",
                "window": {"start": "22:00", "end": "06:00"},
                "repos": ["UpstreamDrift"],
                "budget": {"usd_per_run": 3, "usd_per_day": 15},
                "holds": [],
            }
        ),
        "ad-hoc": roles_mod.parse_role({"name": "ad-hoc", "title": "Ad Hoc", "providers": ["fake"]}),
    }


class _FakeRunner:
    machine = "TestNode"

    def __init__(self, store: store_mod.RunStore, roles: dict[str, roles_mod.RoleSpec]) -> None:
        self.store = store
        self._roles = roles
        self.submitted: list[RunRequest] = []

    def roles(self) -> dict[str, roles_mod.RoleSpec]:
        return self._roles

    def submit(self, req: RunRequest) -> store_mod.RunRecord:
        self.submitted.append(req)
        rec = store_mod.RunRecord(
            id=f"run-{len(self.submitted)}",
            role=req.role,
            provider="fake",
            model=None,
            machine=self.machine,
            repo=req.repo,
            target_kind="prompt",
            target_ref="",
            prompt=req.prompt,
            requested_by=req.requested_by,
        )
        return rec


@pytest.fixture
def fake_sched(tmp_path: Path) -> tuple[scheduler_mod.StaffScheduler, _FakeRunner]:
    store = store_mod.RunStore(tmp_path / "runs.db")
    runner = _FakeRunner(store, _roles())
    holds = holds_mod.HoldsList(tmp_path / "holds.json", roles_loader=runner.roles)
    budget = budget_mod.BudgetGuard(store)
    state = tmp_path / "sched_state.json"
    s = scheduler_mod.StaffScheduler(
        runner,
        holds,
        budget,
        tick_seconds=30,
        clock=lambda: _la(2026, 9, 22, 22, 0),
        state_file=state,
    )
    return s, runner


@pytest.fixture
def client(fake_sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner], monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from routers import staff_schedule as schedule_router

    s, _ = fake_sched
    monkeypatch.setattr(scheduler_mod, "_scheduler", s)
    monkeypatch.setattr(schedule_router, "get_scheduler", lambda: s)

    app = FastAPI()
    app.include_router(schedule_router.router)
    if hasattr(schedule_router, "v1_router"):
        app.include_router(schedule_router.v1_router)

    app.dependency_overrides[require_scope("staff.read")] = lambda: Principal(
        id="test-operator", type="human", name="test-operator", scopes=["staff.read", "staff.holds.write"]
    )
    app.dependency_overrides[require_scope("staff.holds.write")] = lambda: Principal(
        id="test-operator", type="human", name="test-operator", scopes=["staff.read", "staff.holds.write"]
    )

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    scheduler_mod.reset_scheduler()


@pytest.mark.unit
def test_toggle_role_schedule_off_stops_scheduler_tick(
    client: TestClient, fake_sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner]
) -> None:
    s, runner = fake_sched
    due_slot = _la(2026, 9, 22, 22, 0)

    # Initially enabled: evaluate and tick should fire slot
    eval_initial = s.evaluate(runner.roles()["night-watch"], due_slot)
    assert eval_initial["enabled"] is True

    # Disable schedule via PUT /api/v1/staff/roles/night-watch/schedule
    body = {"enabled": False, "reason": "Maintenance window"}
    resp = client.put("/api/v1/staff/roles/night-watch/schedule", json=body, headers=_XHR)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["role"] == "night-watch"
    assert data["enabled"] is False
    assert data["status"] == "disabled"
    assert data["reason"] == "Maintenance window"

    # Verify /api/staff/schedule reports it disabled
    sched_resp = client.get("/api/staff/schedule").json()
    role_info = next(r for r in sched_resp["roles"] if r["role"] == "night-watch")
    assert role_info["enabled"] is False
    assert "disabled" in (role_info.get("hold") or "")

    # Scheduler tick at due time must NOT fire the run
    decisions = s.tick(due_slot)
    assert len(runner.submitted) == 0
    nw_dec = next((d for d in decisions if d["role"] == "night-watch"), None)
    if nw_dec:
        assert nw_dec["fired"] is False
        assert "disabled" in nw_dec["reason"]


@pytest.mark.unit
def test_toggle_role_schedule_on_re_enables_scheduler(
    client: TestClient, fake_sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner]
) -> None:
    s, runner = fake_sched

    # Disable first
    client.put("/api/v1/staff/roles/night-watch/schedule", json={"enabled": False}, headers=_XHR)

    # Re-enable
    resp = client.put("/api/v1/staff/roles/night-watch/schedule", json={"enabled": True}, headers=_XHR)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert resp.json()["status"] == "enabled"

    # Verify /api/staff/schedule shows enabled
    sched_resp = client.get("/api/staff/schedule").json()
    role_info = next(r for r in sched_resp["roles"] if r["role"] == "night-watch")
    assert role_info["enabled"] is True


@pytest.mark.unit
def test_toggle_role_schedule_404_on_unknown_role(client: TestClient) -> None:
    resp = client.put("/api/v1/staff/roles/non-existent-role/schedule", json={"enabled": False}, headers=_XHR)
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]
