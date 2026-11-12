"""Tests for the per-role model override API (epic #3295 slice S2).

Verifies PUT /api/v1/staff/roles/{role}/model:
- A valid override persists and is readable back from the scheduler state.
- An override is rejected (422) when the model is not valid for the role's provider.
- 404 for unknown roles.
- The audit log captures the change (action=role_model_override).
- Clearing the override (model=None) restores the role's YAML-declared default.
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
        "bob": roles_mod.parse_role(
            {
                "name": "bob",
                "title": "Bob",
                "providers": ["claude"],
                "model": "sonnet",
                "repos": ["Runner_Dashboard"],
                "budget": {"usd_per_run": 3, "usd_per_day": 15},
                "holds": [],
            }
        ),
    }


class _FakeRunner:
    machine = "TestNode"

    def __init__(self, store: store_mod.RunStore, roles: dict[str, roles_mod.RoleSpec]) -> None:
        self.store = store
        self._roles = roles
        self.submitted: list[RunRequest] = []

    def roles(self) -> dict[str, roles_mod.RoleSpec]:
        return self._roles


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
def audits() -> list[dict[str, object]]:
    return []


@pytest.fixture
def client(
    fake_sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner],
    audits: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    from routers import staff_schedule as schedule_router

    s, _ = fake_sched
    monkeypatch.setattr(scheduler_mod, "_scheduler", s)
    monkeypatch.setattr(schedule_router, "get_scheduler", lambda: s)
    monkeypatch.setattr(schedule_router, "record_audit", lambda **kw: audits.append(kw) or 1)
    monkeypatch.setattr(schedule_router, "persona_notify", lambda *a, **kw: {"ok": True})

    app = FastAPI()
    app.include_router(schedule_router.router)
    if hasattr(schedule_router, "v1_router"):
        app.include_router(schedule_router.v1_router)

    for scope in ("staff.read", "staff.holds.write"):
        app.dependency_overrides[require_scope(scope)] = lambda: Principal(
            id="test-operator", type="human", name="test-operator", scopes=["staff.read", "staff.holds.write"]
        )

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    scheduler_mod.reset_scheduler()


@pytest.mark.unit
def test_override_model_persists_and_reads_back(
    client: TestClient, fake_sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner], audits: list[dict[str, object]]
) -> None:
    s, _ = fake_sched
    resp = client.put(
        "/api/v1/staff/roles/bob/model",
        json={"model": "claude-opus-5-5", "reason": "epic #3295 cutover"},
        headers=_XHR,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["role"] == "bob"
    assert data["model"] == "claude-opus-5-5"
    assert data["previous"] is None
    assert data["effective_model"] == "claude-opus-5-5"
    assert data["status"] == "overridden"

    assert s.role_model_override("bob") == "claude-opus-5-5"
    assert len(audits) == 1
    assert audits[0]["action"] == "role_model_override"
    assert audits[0]["target"] == "bob"
    assert audits[0]["detail"]["model"] == "claude-opus-5-5"


@pytest.mark.unit
def test_override_model_rejects_invalid_model_for_provider(client: TestClient) -> None:
    resp = client.put("/api/v1/staff/roles/bob/model", json={"model": "not-a-real-model"}, headers=_XHR)
    assert resp.status_code == 422
    assert "not-a-real-model" in resp.json()["detail"]


@pytest.mark.unit
def test_override_model_404_on_unknown_role(client: TestClient) -> None:
    resp = client.put("/api/v1/staff/roles/non-existent-role/model", json={"model": "sonnet"}, headers=_XHR)
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


@pytest.mark.unit
def test_clearing_override_restores_role_default(
    client: TestClient, fake_sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner]
) -> None:
    s, runner = fake_sched
    client.put("/api/v1/staff/roles/bob/model", json={"model": "claude-opus-5-5"}, headers=_XHR)

    resp = client.put("/api/v1/staff/roles/bob/model", json={"model": None}, headers=_XHR)
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] is None
    assert data["previous"] == "claude-opus-5-5"
    assert data["effective_model"] == runner.roles()["bob"].model
    assert data["status"] == "cleared"
    assert s.role_model_override("bob") is None
