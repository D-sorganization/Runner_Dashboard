"""Barb's follow-up sweep reaches all due work fairly and keeps its state (BR-04, Issue #1798).

The sweep read the newest 100 work items, so one overdue item behind 100 newer ones was never
checked. Its debounce, history and counters were process dictionaries, so a restart repeated
follow-ups and two workers acted on the same item. The decision scan stopped at the newest 500
pending proposals. These tests pin the fixed behaviour.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fleet_events import get_event_store
from identity import Principal, require_principal, require_scope
from routers.staff_followup import router as followup_router
from staff.conversations import ConversationStore, get_conversation_store, reset_conversation_store
from staff.decision_sla import overdue_decisions
from staff.followup import FollowupEngine, reset_followup_engine
from staff.holds import HoldsList
from staff.store import get_run_store, reset_store
from staff.work_items import WorkItemStore, get_work_item_store, reset_work_item_store

CLOCK = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    db = tmp_path / "staff.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    monkeypatch.setenv("RUNNER_DASHBOARD_PUSH_DB", str(tmp_path / "push.sqlite3"))
    for reset in (reset_store, reset_work_item_store, reset_conversation_store, reset_followup_engine):
        reset()
    stores = {
        "runs": get_run_store(db),
        "work": get_work_item_store(db),
        "conv": get_conversation_store(db),
    }

    def make_engine() -> FollowupEngine:
        """A fresh engine on the same database: a restarted process, or a second sweep worker."""
        return FollowupEngine(
            run_store=stores["runs"],
            work_item_store=stores["work"],
            conversation_store=stores["conv"],
            event_store=get_event_store(),
            sweep_interval_seconds=300,
            holds=HoldsList(tmp_path / "holds.json", roles_loader=dict),
        )

    yield {**stores, "make_engine": make_engine}
    for reset in (reset_followup_engine, reset_conversation_store, reset_work_item_store, reset_store):
        reset()


def _overdue(work: WorkItemStore, minutes: int = 30, title: str = "overdue") -> str:
    due = _iso(CLOCK - timedelta(minutes=minutes))
    return work.create_work_item(title=title, requested_by="user", expected_by=due).id


@pytest.mark.unit
def test_an_old_overdue_item_is_checked_behind_newer_work(env: dict[str, Any]) -> None:
    old = _overdue(env["work"])
    for i in range(150):
        env["work"].create_work_item(title=f"new {i}", requested_by="user")

    env["make_engine"]().sweep(now=CLOCK)

    assert [r.action_taken for r in env["make_engine"]().get_followup_records(old)] == ["overdue_ping"]


@pytest.mark.unit
def test_every_overdue_item_among_a_thousand_mixed_records_is_checked(env: dict[str, Any]) -> None:
    work: WorkItemStore = env["work"]
    overdue: list[str] = []
    for i in range(1000):
        if i % 3 == 0:
            done = work.create_work_item(title=f"done {i}", requested_by="user", expected_by=_iso(CLOCK))
            work.transition_state(done.id, "cancelled", actor="user")
        elif i % 3 == 1:
            overdue.append(_overdue(work, minutes=1 + i % 90, title=f"late {i}"))
        else:
            later = _iso(CLOCK + timedelta(hours=1))
            work.create_work_item(title=f"future {i}", requested_by="user", expected_by=later)

    engine = env["make_engine"]()
    result = engine.sweep(now=CLOCK)

    missed = [wid for wid in overdue if not engine.get_followup_records(wid)]
    assert missed == []
    assert result.backlog is not None
    assert (result.backlog.active, result.backlog.overdue) == (666, len(overdue))
    assert result.backlog.oldest_due_age_seconds == 89 * 60  # the latest fixture item is 89 minutes late


@pytest.mark.unit
def test_a_restarted_engine_does_not_repeat_a_follow_up_within_the_interval(env: dict[str, Any]) -> None:
    wid = _overdue(env["work"])
    env["make_engine"]().sweep(now=CLOCK)

    restarted = env["make_engine"]()
    assert restarted.sweep(now=CLOCK + timedelta(seconds=5)).followups == []
    assert len(restarted.get_followup_records(wid)) == 1

    restarted.sweep(now=CLOCK + timedelta(seconds=310))
    assert len(restarted.get_followup_records(wid)) == 2


@pytest.mark.unit
def test_concurrent_sweep_workers_claim_disjoint_work(env: dict[str, Any]) -> None:
    ids = [_overdue(env["work"], minutes=m) for m in range(1, 41)]
    workers = [env["make_engine"]() for _ in range(4)]
    barrier = threading.Barrier(len(workers))
    acted: list[list[str]] = []

    def run(engine: FollowupEngine) -> None:
        barrier.wait()
        acted.append([f.target_id for f in engine.sweep(now=CLOCK).followups])

    threads = [threading.Thread(target=run, args=(w,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    flat = [wid for batch in acted for wid in batch]
    assert sorted(flat) == sorted(ids), "each overdue item is acted on exactly once across workers"
    assert all(len(workers[0].get_followup_records(wid)) == 1 for wid in ids)


@pytest.mark.unit
def test_the_digest_counts_survive_a_restart(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    work: WorkItemStore = env["work"]
    item = work.create_work_item(title="misrouted", requested_by="user", owner_role="no-such-role")
    monkeypatch.setattr("staff.followup.load_roles", lambda: {"librarian": {}})
    env["make_engine"]().sweep(now=CLOCK)

    digest = env["make_engine"]().get_daily_digest(now=CLOCK + timedelta(minutes=1))

    assert digest.rerouted == 1
    assert work.get_work_item(item.id).owner_role == "librarian"  # type: ignore[union-attr]


def _propose(conv: ConversationStore, decide_by: datetime) -> str:
    th = conv.create_thread(title="SLA", kind="direct", participants=["planner", "user"])
    msg = conv.add_message(thread_id=th.id, author_kind="user", author="user", body_md="proposal")
    return conv.create_proposal(message_id=msg.id, thread_id=th.id, action="test.noop", decide_by=_iso(decide_by)).id


@pytest.mark.unit
def test_overdue_decisions_are_paged_past_the_old_500_cap(env: dict[str, Any]) -> None:
    conv: ConversationStore = env["conv"]
    oldest = _propose(conv, CLOCK - timedelta(days=3))
    for i in range(520):
        _propose(conv, CLOCK - timedelta(minutes=1 + i % 50))
    _propose(conv, CLOCK + timedelta(hours=1))

    due = overdue_decisions(conv, CLOCK, page=64)

    assert len(due) == 521
    assert due[0].id == oldest


@pytest.mark.unit
def test_the_status_route_reports_the_last_sweep_backlog(env: dict[str, Any]) -> None:
    _overdue(env["work"], minutes=45)
    engine = env["make_engine"]()
    engine.sweep(now=CLOCK)
    app = FastAPI()
    app.include_router(followup_router, prefix="/api/v1/staff")
    principal = Principal(id="op", type="human", name="Operator", roles=["admin"])
    app.dependency_overrides[require_principal] = lambda: principal
    app.dependency_overrides[require_scope("staff.read")] = lambda: principal
    from staff.followup import get_followup_engine

    get: Callable[[], FollowupEngine] = lambda: engine  # noqa: E731
    app.dependency_overrides[get_followup_engine] = get

    backlog = TestClient(app).get("/api/v1/staff/followup/status").json()["backlog"]

    assert (backlog["active"], backlog["overdue"], backlog["oldest_due_age_seconds"]) == (1, 1, 45 * 60)
    assert backlog["duration_ms"] >= 0
