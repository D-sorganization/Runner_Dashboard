"""WP19 D-13 (#4274): scheduler leader lease scope and failover double-fire.

Two defects from the adversarial review:

* ``POST /api/staff/schedule/leader-lease`` mutates the lease but was gated by
  ``staff.read``, so any viewer (or an anonymous tailnet caller on a node with
  no fleet token) could take or renew the lease.
* Slot cursors are per-machine, so a host that took the lease over re-fired a
  slot the previous leader had already handled.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from staff import budget as budget_mod
from staff import holds as holds_mod
from staff import roles as roles_mod
from staff import scheduler as scheduler_mod
from staff import scheduler_leader_lease as lease_mod
from staff import store as store_mod

from tests.api.test_staff_schedule import _XHR, _FakeRunner, _la

CHAIN = "hub-a,node-b"
LEASE_URL = "/api/staff/schedule/leader-lease"


def _node(tmp_path: Path, machine: str, clock: dict[str, datetime]) -> tuple[scheduler_mod.StaffScheduler, _FakeRunner]:
    """One fleet host: its own run store, state file and scheduler, sharing ``clock``."""
    home = tmp_path / machine
    home.mkdir()
    store = store_mod.RunStore(home / "runs.sqlite3")
    role = roles_mod.parse_role(
        {
            "name": "night-watch",
            "providers": ["fake"],
            "schedule": "0 22 * * *",
            "repos": ["UpstreamDrift"],
            "budget": {"usd_per_run": 3, "usd_per_day": 15},
        }
    )
    runner = _FakeRunner(store, {"night-watch": role})
    runner.machine = machine
    holds = holds_mod.HoldsList(home / "holds.json", roles_loader=dict)
    holds.replace([])
    guard = budget_mod.BudgetGuard(store, alert_sink=lambda *_: None, clock=lambda: clock["now"])
    sched = scheduler_mod.StaffScheduler(
        runner,  # type: ignore[arg-type]
        holds,
        guard,
        tick_seconds=30,
        clock=lambda: clock["now"],
        state_file=home / "state.json",
    )
    return sched, runner


@pytest.fixture
def fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[scheduler_mod.StaffScheduler, _FakeRunner, scheduler_mod.StaffScheduler, _FakeRunner, dict[str, datetime]]:
    """Hub and failover node; the hub's state file is the lease authority for both."""
    clock = {"now": _la(2026, 9, 22, 21, 59, 50)}
    hub, hub_runner = _node(tmp_path, "hub-a", clock)
    node, node_runner = _node(tmp_path, "node-b", clock)
    # Anchor both cursors before the slot while fencing is still off (as a status poll does).
    hub.status()
    node.status()
    monkeypatch.setenv("STAFF_SCHEDULER_LEADER_CHAIN", CHAIN)

    def _ask_hub(_local: Any, machine: str, now: datetime, ttl_seconds: float) -> dict[str, Any]:
        return hub.acquire_leader_lease(machine, now=now, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(lease_mod, "acquire_remote_leader_lease", _ask_hub)
    return hub, hub_runner, node, node_runner, clock


@pytest.mark.unit
def test_failover_does_not_refire_slot_the_old_leader_fired(fleet: Any) -> None:
    hub, hub_runner, node, node_runner, clock = fleet
    clock["now"] = _la(2026, 9, 22, 22, 0, 5)
    assert [d["fired"] for d in hub.tick()] == [True]
    assert node.tick() == []  # hub holds the lease
    # The hub scheduler dies; its lease (90 s) expires and node-b takes over.
    clock["now"] = _la(2026, 9, 22, 22, 5, 0)
    assert node.tick() == []
    assert node.leader_status()["holder"] == "" and hub.leader_status()["holder"] == "node-b"
    assert node_runner.submitted == [], "new leader re-fired the 22:00 slot the old leader already fired"
    assert len(hub_runner.submitted) == 1


@pytest.mark.unit
def test_returning_primary_does_not_refire_slot_the_failover_fired(fleet: Any) -> None:
    hub, hub_runner, node, node_runner, clock = fleet
    # Hub scheduler is down across the slot; node-b leads and fires it.
    clock["now"] = _la(2026, 9, 22, 22, 0, 5)
    assert [d["fired"] for d in node.tick()] == [True]
    # Hub comes back and preempts as the higher-priority host.
    clock["now"] = _la(2026, 9, 22, 22, 0, 40)
    assert hub.tick() == []
    assert hub.leader_status()["holder"] == "hub-a"
    assert hub_runner.submitted == [], "returning primary re-fired the slot the failover host fired"
    assert len(node_runner.submitted) == 1
    # The next slot is the hub's and fires exactly once.
    clock["now"] = _la(2026, 9, 23, 22, 0, 5)
    assert [d["fired"] for d in hub.tick()] == [True]


@pytest.mark.unit
def test_failover_still_fires_slot_the_old_leader_never_reached(fleet: Any) -> None:
    hub, hub_runner, node, node_runner, clock = fleet
    assert hub.tick() == []  # 21:59:50: hub renews the lease, then dies before the slot
    clock["now"] = _la(2026, 9, 22, 22, 5, 0)
    assert [d["fired"] for d in node.tick()] == [True]
    assert hub_runner.submitted == [] and len(node_runner.submitted) == 1


@pytest.mark.unit
def test_lease_reply_reports_how_far_the_peer_leader_got(fleet: Any) -> None:
    hub, _, _, _, _ = fleet
    t0 = _la(2026, 9, 22, 22, 0, 0)
    first = hub.acquire_leader_lease("hub-a", now=t0, ttl_seconds=30)
    assert first["granted"] is True and first.get("handled_through") is None
    denied = hub.acquire_leader_lease("node-b", now=_la(2026, 9, 22, 22, 0, 10), ttl_seconds=30)
    assert denied["granted"] is False and denied["handled_through"] == t0.isoformat()
    takeover = hub.acquire_leader_lease("node-b", now=_la(2026, 9, 22, 22, 1, 0), ttl_seconds=30)
    assert takeover["granted"] is True and takeover["handled_through"] == t0.isoformat()
    renewed = hub.acquire_leader_lease("node-b", now=_la(2026, 9, 22, 22, 1, 20), ttl_seconds=30)
    assert renewed["reason"] == "renewed" and renewed["handled_through"] == t0.isoformat()


# ── scope (claim a) ──────────────────────────────────────────────────────
@pytest.fixture
def lease_client(fleet: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, FastAPI]]:
    from routers import staff_schedule as schedule_router  # noqa: PLC0415

    monkeypatch.setattr(scheduler_mod, "_scheduler", fleet[0])
    app = FastAPI()
    app.include_router(schedule_router.router)
    with TestClient(app) as c:
        yield c, app
    app.dependency_overrides.clear()
    scheduler_mod.reset_scheduler()


def _as(app: FastAPI, role: str) -> None:
    from identity import Principal, require_principal  # noqa: PLC0415

    app.dependency_overrides[require_principal] = lambda: Principal(id=f"t-{role}", type="bot", name=role, roles=[role])


@pytest.mark.unit
def test_read_scoped_caller_cannot_take_the_leader_lease(lease_client: tuple[TestClient, FastAPI]) -> None:
    client, app = lease_client
    _as(app, "viewer")
    r = client.post(LEASE_URL, json={"machine": "hub-a", "ttl_seconds": 3600}, headers=_XHR)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["required_scope"] == "staff.dispatch"
    assert client.get("/api/staff/schedule").json()["leader_lease"]["holder"] == ""


@pytest.mark.unit
def test_fleet_peer_can_still_take_the_leader_lease(lease_client: tuple[TestClient, FastAPI]) -> None:
    client, app = lease_client
    _as(app, "fleet-peer")
    r = client.post(LEASE_URL, json={"machine": "node-b", "ttl_seconds": 30}, headers=_XHR)
    assert r.status_code == 200, r.text
    assert r.json()["granted"] is True and r.json()["holder"] == "node-b"
