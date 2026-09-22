"""Scheduled-role liveness (epic #1192, issue #1209).

The state machine is driven with a frozen clock over a seeded temp run store
and an in-memory scheduler state; the routes are exercised through the staff
router with the auth dependencies overridden; the hub merge uses fake peers.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fleet_events import EventStore, FleetEvent
from staff import adapters as adapters_mod
from staff import fleet as fleet_mod
from staff import liveness as liveness_mod
from staff import roles as roles_mod
from staff import runner as runner_mod
from staff import store as store_mod

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
DAY = 86400


def _roles() -> dict[str, roles_mod.RoleSpec]:
    return {
        "night-watch": roles_mod.parse_role({"name": "night-watch", "providers": ["claude"], "schedule": "0 22 * * *"}),
        "hourly": roles_mod.parse_role({"name": "hourly", "providers": ["claude"], "schedule": "0 * * * *"}),
        "manual": roles_mod.parse_role({"name": "manual", "providers": ["claude"]}),
        "retired": roles_mod.parse_role(
            {"name": "retired", "providers": ["claude"], "schedule": "0 1 * * *", "retired": True}
        ),
    }


def _iso(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


def _run(store: store_mod.RunStore, role: str, status: str, age_seconds: int, run_id: str) -> None:
    when = NOW - timedelta(seconds=age_seconds)
    ended = _iso(when) if status not in store_mod.ACTIVE_STATUSES else None
    rec = store_mod.RunRecord(
        id=run_id, role=role, provider="claude", model=None, machine="Desk", repo="", target_kind="prompt",
        target_ref="", prompt="x", status=status, created_at=_iso(when), ended_at=ended,
    )  # fmt: skip
    store.create_run(rec)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[store_mod.RunStore]:
    s = store_mod.RunStore(tmp_path / "runs.sqlite3")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _fresh_debounce() -> Iterator[None]:
    liveness_mod.reset_dead_notifications()
    yield
    liveness_mod.reset_dead_notifications()


# ── pure function ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_expected_interval_is_gap_between_next_two_fires() -> None:
    assert liveness_mod.expected_interval_seconds("0 22 * * *", NOW) == DAY
    assert liveness_mod.expected_interval_seconds("0 * * * *", NOW) == 3600
    assert liveness_mod.expected_interval_seconds("not a cron", NOW) is None


@pytest.mark.unit
def test_only_dispatchable_scheduled_roles_are_reported(store: store_mod.RunStore) -> None:
    rows = liveness_mod.compute_liveness(_roles(), store, {}, NOW)
    assert [r["role"] for r in rows] == ["hourly", "night-watch"]
    assert all(r["status"] == "never" for r in rows)
    assert rows[1]["expected_interval_seconds"] == DAY and rows[1]["next_fire"] is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("age_seconds", "expected"),
    [(3600, "ok"), (int(1.5 * DAY), "ok"), (int(1.5 * DAY) + 1, "late"), (3 * DAY, "late"), (3 * DAY + 1, "dead")],
)
def test_last_success_age_drives_ok_late_dead(store: store_mod.RunStore, age_seconds: int, expected: str) -> None:
    _run(store, "night-watch", "succeeded", age_seconds, "s1")
    _run(store, "night-watch", "failed", 60, "f1")  # a newer failure does not reset the clock
    row = next(r for r in liveness_mod.compute_liveness(_roles(), store, {}, NOW) if r["role"] == "night-watch")
    assert row["status"] == expected
    assert row["age_seconds"] == age_seconds
    assert row["last_success"] == _iso(NOW - timedelta(seconds=age_seconds)).replace("Z", "+00:00")
    assert row["last_attempt"] == _iso(NOW - timedelta(seconds=60)).replace("Z", "+00:00")


@pytest.mark.unit
def test_fired_but_never_succeeded_is_dead_unless_attempt_still_active(store: store_mod.RunStore) -> None:
    state = {"night-watch": {"cursor": _iso(NOW), "last_fired": _iso(NOW - timedelta(minutes=5))}}
    row = liveness_mod.compute_liveness(_roles(), store, state, NOW)[1]
    assert row["status"] == "dead" and row["last_fired"] is not None and row["last_success"] is None
    _run(store, "night-watch", "running", 120, "r1")
    row = liveness_mod.compute_liveness(_roles(), store, state, NOW)[1]
    assert row["status"] == "late"
    _run(store, "night-watch", "failed", 30, "f1")
    assert liveness_mod.compute_liveness(_roles(), store, state, NOW)[1]["status"] == "dead"


@pytest.mark.unit
def test_load_scheduler_state_tolerates_missing_and_bad_files(tmp_path: Path) -> None:
    assert liveness_mod.load_scheduler_state(tmp_path / "missing.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert liveness_mod.load_scheduler_state(bad) == {}
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"night-watch": {"last_fired": "x"}, "junk": 1}), encoding="utf-8")
    assert liveness_mod.load_scheduler_state(good) == {"night-watch": {"last_fired": "x"}}


# ── fleet event on dead ──────────────────────────────────────────────────
@pytest.mark.unit
def test_fleet_event_accepts_staff_role_dead_kind() -> None:
    ev = FleetEvent(ts=1, severity="warning", kind="staff_role_dead", title="x", node="Desk")
    assert ev.to_dict()["kind"] == "staff_role_dead"


@pytest.mark.unit
def test_notify_dead_records_one_event_per_role_per_six_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    events = EventStore(capacity=10)
    monkeypatch.setattr(liveness_mod, "get_event_store", lambda: events)
    rows: list[dict[str, Any]] = [
        {"role": "night-watch", "status": "dead", "schedule": "0 22 * * *", "last_success": None},
        {"role": "hourly", "status": "late", "schedule": "0 * * * *", "last_success": None},
    ]
    t0 = 1_000_000.0
    assert liveness_mod.notify_dead(rows, "Desk", clock=t0) == ["night-watch"]
    assert liveness_mod.notify_dead(rows, "Desk", clock=t0 + 5 * 3600) == []
    assert liveness_mod.notify_dead(rows, "Desk", clock=t0 + 6 * 3600) == ["night-watch"]
    recorded = events.recent()
    assert len(recorded) == 2
    assert recorded[0].kind == "staff_role_dead" and recorded[0].severity == "warning"
    assert recorded[0].node == "Desk" and "night-watch" in recorded[0].title


# ── hub aggregation ──────────────────────────────────────────────────────
def _board(machine: str, liveness: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "machine": machine,
        "running": [],
        "queued": [],
        "recent": [],
        "spend_today_usd": {},
        "providers": {"claude": True},
        "liveness": liveness or [],
    }


@pytest.mark.unit
async def test_aggregate_board_carries_liveness_and_alerts_from_online_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    dead = {"role": "night-watch", "status": "dead"}
    ok = {"role": "hourly", "status": "ok"}

    async def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        if "og:" in url:
            return _board("OGLaptop", [dead, ok])
        raise ConnectionError("boom")

    monkeypatch.setattr(fleet_mod, "get_json", fake_get)
    merged = await fleet_mod.aggregate_board(
        _board("Desk", [{"role": "hourly", "status": "late"}]),
        {"OGLaptop": "http://og:8321", "ControlTower": "http://ct:8321"},
    )
    assert merged["machines"]["OGLaptop"]["liveness"] == [dead, ok]
    assert merged["liveness_alerts"] == [
        {"role": "hourly", "status": "late", "machine": "Desk"},
        {"role": "night-watch", "status": "dead", "machine": "OGLaptop"},
    ]


# ── routes ───────────────────────────────────────────────────────────────
@pytest.fixture
def staff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[runner_mod.StaffRunner]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "no-roles"))
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(tmp_path / "wt"))
    monkeypatch.setenv("STAFF_SCHEDULE_STATE", str(tmp_path / "state.json"))
    fake = adapters_mod.ProviderAdapter(
        provider_id="claude", label="Fake claude", executable=sys.executable, argv=("-c", "pass"), json_lines=True
    )
    store_mod.reset_store()
    runner_mod.reset_runner()
    adapters = {**adapters_mod.ADAPTERS, "claude": fake}
    r = runner_mod.StaffRunner(store=store_mod.RunStore(tmp_path / "runs.sqlite3"), adapters=adapters, machine="Desk")
    monkeypatch.setattr(r, "roles", _roles)
    monkeypatch.setattr(runner_mod, "_runner", r)
    yield r
    store_mod.reset_store()
    runner_mod.reset_runner()


@pytest.fixture
def client(staff: runner_mod.StaffRunner) -> Iterator[TestClient]:
    from identity import require_fleet_peer, require_orchestrator_peer  # noqa: PLC0415
    from routers import staff as staff_router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(staff_router.router)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    app.dependency_overrides[require_orchestrator_peer] = lambda: "test-orchestrator"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.unit
def test_board_and_summary_expose_liveness(
    client: TestClient, staff: runner_mod.StaffRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_mod, "peer_nodes", lambda: {})
    events = EventStore(capacity=10)
    monkeypatch.setattr(liveness_mod, "get_event_store", lambda: events)
    (tmp_path / "state.json").write_text(
        json.dumps({"night-watch": {"cursor": "2026-09-01T00:00:00+00:00", "last_fired": "2026-09-01T00:00:00+00:00"}}),
        encoding="utf-8",
    )
    board = client.get("/api/staff/board").json()
    by_role = {r["role"]: r for r in board["liveness"]}
    assert set(by_role) == {"hourly", "night-watch"}
    assert by_role["hourly"]["status"] == "never"
    assert by_role["night-watch"]["status"] == "dead"
    assert [e.kind for e in events.recent()] == ["staff_role_dead"]
    summary = client.get("/api/staff/summary").json()
    assert [(a["role"], a["status"], a["machine"]) for a in summary["liveness_alerts"]] == [
        ("night-watch", "dead", "Desk")
    ]
    assert "holds" in summary and "roles" in summary  # existing keys untouched
