"""Tests for Barb Follow-up Engine (SC-C4, Issue #1327).

Acceptance criteria:
1. Simulated stalled run -> Barb retries, then escalates after the second failure
   with a message in Barb's thread.
2. Killing the sweep raises the watchdog alert within two intervals.
3. No item stays active past SLA + one interval without a follow-up record (property test).
4. Debounce and idempotency: at most one follow-up per item per interval;
   follow-ups are recorded on the item.
5. Auth expired triggers owner action item; needs input asks owner in Barb's thread;
   wrong owner re-routes.
6. Daily digest counts: closed, retried, re-routed, escalated, still_open.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fleet_events import EventStore, get_event_store
from identity import Principal, require_principal, require_scope
from routers.staff_followup import router as followup_router
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
    reset_conversation_store,
)
from staff.followup import (
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    FollowupDigest,
    FollowupEngine,
    reset_followup_engine,
)
from staff.store import RunRecord, RunStore, get_run_store, reset_store
from staff.work_items import (
    WorkItemRecord,
    WorkItemStore,
    get_work_item_store,
    reset_work_item_store,
)

TEST_PRINCIPAL = Principal(
    id="test-operator",
    type="human",
    name="Test Operator",
    roles=["admin"],
    scopes=["staff.read", "staff.write", "staff.chat", "staff.dispatch"],
)


@pytest.fixture
def test_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Any]]:
    db_path = tmp_path / "staff_test.sqlite3"
    push_db = tmp_path / "push_test.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_path))
    monkeypatch.setenv("RUNNER_DASHBOARD_PUSH_DB", str(push_db))

    reset_store()
    reset_work_item_store()
    reset_conversation_store()
    reset_followup_engine()

    r_store = get_run_store(db_path)
    w_store = get_work_item_store(db_path)
    c_store = get_conversation_store(db_path)
    e_store = get_event_store()

    engine = FollowupEngine(
        run_store=r_store,
        work_item_store=w_store,
        conversation_store=c_store,
        event_store=e_store,
        sweep_interval_seconds=300,
    )

    yield {
        "engine": engine,
        "run_store": r_store,
        "work_item_store": w_store,
        "conversation_store": c_store,
        "event_store": e_store,
        "db_path": db_path,
    }

    reset_followup_engine()
    reset_conversation_store()
    reset_work_item_store()
    reset_store()


@pytest.fixture
def client(test_env: dict[str, Any]) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(followup_router, prefix="/api/v1/staff")

    app.dependency_overrides[require_principal] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.write")] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.dispatch")] = lambda: TEST_PRINCIPAL

    with TestClient(app) as tc:
        yield tc

    app.dependency_overrides.clear()


@pytest.mark.unit
def test_simulated_stalled_run_retries_then_escalates(test_env: dict[str, Any]) -> None:
    """AC 1: Stalled run -> Barb retries on first failure, then escalates after second failure."""
    engine: FollowupEngine = test_env["engine"]
    r_store: RunStore = test_env["run_store"]
    w_store: WorkItemStore = test_env["work_item_store"]
    c_store: ConversationStore = test_env["conversation_store"]

    clock = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)

    # 1. Create a work item and an associated stalled run (attempt 1)
    item = w_store.create_work_item(
        title="Optimize trajectory calculation",
        requested_by="operator-1",
        owner_role="librarian",
    )
    run_1 = RunRecord(
        id="run-stall-001",
        role="librarian",
        provider="claude",
        model=None,
        machine="Desk",
        repo="UpstreamDrift",
        target_kind="issue",
        target_ref="#100",
        prompt="Optimize trajectory calculation",
        status="failed",
        created_at=clock.isoformat().replace("+00:00", "Z"),
        attempt=1,
        max_attempts=2,
        failure_class="stalled",
    )
    r_store.create_run(run_1)
    w_store.add_link(item.id, "runs", run_1.id)
    w_store.transition_state(
        item.id, "in_progress", actor="barb", reason="Run dispatched"
    )

    # First sweep at clock
    res1 = engine.sweep(now=clock)
    assert res1.actions_count.get("retry", 0) == 1

    # Verify a retry was initiated
    item_after_1 = w_store.get_work_item(item.id)
    assert item_after_1 is not None
    assert len(item_after_1.links.get("runs", [])) == 2
    retry_run_id = item_after_1.links["runs"][-1]
    assert retry_run_id != run_1.id

    retry_run = r_store.get_run(retry_run_id)
    assert retry_run is not None
    assert retry_run.attempt == 2
    assert retry_run.retry_of == run_1.id

    # 2. Advance clock past sweep interval and simulate that attempt 2 also stalled
    clock = clock + timedelta(seconds=DEFAULT_SWEEP_INTERVAL_SECONDS + 10)
    r_store.update_run(retry_run_id, status="failed", failure_class="stalled")

    # Second sweep: Barb should escalate after the second failure
    res2 = engine.sweep(now=clock)
    assert res2.actions_count.get("escalate", 0) == 1

    item_after_2 = w_store.get_work_item(item.id)
    assert item_after_2 is not None
    assert item_after_2.state == "escalated"

    # Verify escalation message posted to Barb's thread
    threads = c_store.list_threads()
    barb_thread = next(
        (t for t in threads if "barb" in [p.lower() for p in t.participants]), None
    )
    assert barb_thread is not None

    messages = c_store.list_messages(barb_thread.id)
    assert len(messages) >= 1
    last_msg = messages[-1]
    assert "escalat" in last_msg.body_md.lower()
    assert item.title in last_msg.body_md


@pytest.mark.unit
def test_watchdog_detects_killed_sweep(test_env: dict[str, Any]) -> None:
    """AC 2: Killing the sweep raises watchdog alert within two intervals."""
    engine: FollowupEngine = test_env["engine"]
    e_store: EventStore = test_env["event_store"]

    clock = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

    # Initial sweep records liveness
    engine.sweep(now=clock)
    assert engine.last_sweep_at is not None

    # Watchdog within 1 interval is healthy
    clock_1 = clock + timedelta(seconds=DEFAULT_SWEEP_INTERVAL_SECONDS)
    status_1 = engine.check_watchdog(now=clock_1)
    assert status_1["ok"] is True
    assert status_1["alert_fired"] is False

    # Simulate sweep killed/stopped: clock advances past 2 intervals (e.g. 2.1 intervals)
    clock_2 = clock + timedelta(seconds=2 * DEFAULT_SWEEP_INTERVAL_SECONDS + 5)
    status_2 = engine.check_watchdog(now=clock_2)
    assert status_2["ok"] is False
    assert status_2["alert_fired"] is True
    assert status_2["missed_intervals"] >= 2

    # Check fleet event was emitted with severity critical
    events = e_store.recent(limit=10)
    watchdog_events = [ev for ev in events if ev.kind == "barb_followup_watchdog"]
    assert len(watchdog_events) >= 1
    assert watchdog_events[0].severity == "critical"


@pytest.mark.unit
def test_property_no_item_stays_active_past_sla_without_followup(
    test_env: dict[str, Any],
) -> None:
    """AC 3 (Property Test): No item stays active past SLA + one interval without a follow-up record."""
    engine: FollowupEngine = test_env["engine"]
    w_store: WorkItemStore = test_env["work_item_store"]

    base_time = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
    interval = DEFAULT_SWEEP_INTERVAL_SECONDS

    # Create 10 work items with varying SLA deadlines (some in the past, some in the future)
    created_items: list[WorkItemRecord] = []
    offsets_minutes = [-120, -60, -10, -2, 5, 30, 60, 120, 240, 480]

    for idx, offset in enumerate(offsets_minutes):
        deadline = base_time + timedelta(minutes=offset)
        wi = w_store.create_work_item(
            title=f"Task #{idx} offset {offset}m",
            requested_by="test-agent",
            owner_role="librarian",
            expected_by=deadline.isoformat().replace("+00:00", "Z"),
        )
        created_items.append(wi)

    # Advance time to base_time + 1 interval
    sweep_time = base_time + timedelta(seconds=interval)
    engine.sweep(now=sweep_time)

    # Invariant check: for every active item whose SLA was <= sweep_time - interval (i.e. past SLA + 1 interval),
    # it MUST have a follow-up record recorded on the item.
    all_items = w_store.list_work_items(limit=50)
    for it in all_items:
        if it.state in ("done", "cancelled"):
            continue
        if not it.expected_by:
            continue
        expected_dt = datetime.fromisoformat(it.expected_by.replace("Z", "+00:00"))
        # If it passed SLA + interval
        if expected_dt + timedelta(seconds=interval) <= sweep_time:
            followup_records = engine.get_followup_records(it.id)
            assert (
                len(followup_records) >= 1
            ), f"Item {it.id} past SLA + 1 interval had no follow-up record!"


@pytest.mark.unit
def test_debounce_and_idempotency(test_env: dict[str, Any]) -> None:
    """AC 4: Debounce and idempotency: at most one follow-up per item per interval."""
    engine: FollowupEngine = test_env["engine"]
    w_store: WorkItemStore = test_env["work_item_store"]

    clock = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)

    # Create an overdue item
    overdue_time = clock - timedelta(minutes=15)
    item = w_store.create_work_item(
        title="Stale database migration",
        requested_by="operator-1",
        owner_role="librarian",
        expected_by=overdue_time.isoformat().replace("+00:00", "Z"),
    )

    # Sweep 1
    engine.sweep(now=clock)
    records1 = engine.get_followup_records(item.id)
    assert len(records1) == 1

    # Sweep 2 immediately afterwards (same clock or +5s)
    engine.sweep(now=clock + timedelta(seconds=5))
    records2 = engine.get_followup_records(item.id)
    assert (
        len(records2) == 1
    ), "Debounce failed: second sweep in same interval duplicated follow-up!"

    # Advance clock past interval and sweep 3
    clock_next = clock + timedelta(seconds=DEFAULT_SWEEP_INTERVAL_SECONDS + 10)
    engine.sweep(now=clock_next)
    records3 = engine.get_followup_records(item.id)
    assert len(records3) == 2, "Expected new follow-up after interval elapsed."


@pytest.mark.unit
def test_auth_expired_playbook(test_env: dict[str, Any]) -> None:
    """AC 5a: Run failing with auth_expired creates owner action item and thread alert."""
    engine: FollowupEngine = test_env["engine"]
    r_store: RunStore = test_env["run_store"]
    w_store: WorkItemStore = test_env["work_item_store"]
    c_store: ConversationStore = test_env["conversation_store"]

    clock = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)

    item = w_store.create_work_item(
        title="Deploy release v4.11",
        requested_by="operator-1",
        owner_role="operator",
    )
    run = RunRecord(
        id="run-auth-001",
        role="operator",
        provider="codex",
        model=None,
        machine="Desk",
        repo="Runner_Dashboard",
        target_kind="prompt",
        target_ref="",
        prompt="Deploy release",
        status="failed",
        failure_class="auth_expired",
        created_at=clock.isoformat().replace("+00:00", "Z"),
        attempt=1,
    )
    r_store.create_run(run)
    w_store.add_link(item.id, "runs", run.id)

    res = engine.sweep(now=clock)
    assert res.actions_count.get("auth_alert", 0) == 1

    # Verify Barb's thread has auth notice
    threads = c_store.list_threads()
    barb_thread = next(
        (t for t in threads if "barb" in [p.lower() for p in t.participants]), None
    )
    assert barb_thread is not None
    msgs = c_store.list_messages(barb_thread.id)
    assert any("auth" in m.body_md.lower() for m in msgs)


@pytest.mark.unit
def test_needs_input_playbook(test_env: dict[str, Any]) -> None:
    """AC 5b: Work item in waiting_on_user asks owner in Barb's thread."""
    engine: FollowupEngine = test_env["engine"]
    w_store: WorkItemStore = test_env["work_item_store"]
    c_store: ConversationStore = test_env["conversation_store"]

    clock = datetime(2026, 9, 25, 16, 0, tzinfo=UTC)

    item = w_store.create_work_item(
        title="Confirm deletion of legacy worktree",
        requested_by="operator-1",
        owner_role="librarian",
    )
    w_store.transition_state(item.id, "in_progress", actor="barb", reason="Started")
    w_store.transition_state(
        item.id, "waiting_on_user", actor="librarian", reason="Confirmation needed"
    )

    res = engine.sweep(now=clock)
    assert res.actions_count.get("ask_owner", 0) == 1

    threads = c_store.list_threads()
    barb_thread = next(
        (t for t in threads if "barb" in [p.lower() for p in t.participants]), None
    )
    assert barb_thread is not None
    msgs = c_store.list_messages(barb_thread.id)
    assert any(
        "input needed" in m.body_md.lower() or "confirm" in m.body_md.lower()
        for m in msgs
    )


@pytest.mark.unit
def test_wrong_owner_reroute_playbook(test_env: dict[str, Any]) -> None:
    """AC 5c: Work item with invalid or unknown owner role is re-routed to appropriate role."""
    engine: FollowupEngine = test_env["engine"]
    w_store: WorkItemStore = test_env["work_item_store"]

    clock = datetime(2026, 9, 25, 17, 0, tzinfo=UTC)

    item = w_store.create_work_item(
        title="Run biomechanics model simulation",
        requested_by="operator-1",
        owner_role="non_existent_role",
    )

    res = engine.sweep(now=clock)
    assert res.actions_count.get("reroute", 0) == 1

    updated = w_store.get_work_item(item.id)
    assert updated is not None
    assert updated.owner_role != "non_existent_role"


@pytest.mark.unit
def test_daily_digest_counts(test_env: dict[str, Any]) -> None:
    """AC 6: Daily digest counts: closed, retried, re-routed, escalated, still_open."""
    engine: FollowupEngine = test_env["engine"]
    w_store: WorkItemStore = test_env["work_item_store"]

    clock = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)

    # 1. Closed item
    wi_done = w_store.create_work_item(title="Item done", requested_by="user")
    w_store.transition_state(wi_done.id, "in_progress", actor="user")
    w_store.transition_state(wi_done.id, "done", actor="user")

    # 2. Escalated item
    wi_esc = w_store.create_work_item(title="Item escalated", requested_by="user")
    w_store.transition_state(wi_esc.id, "in_progress", actor="user")
    w_store.transition_state(wi_esc.id, "escalated", actor="user")

    # 3. Still open item
    w_store.create_work_item(title="Item open", requested_by="user")

    digest = engine.get_daily_digest(now=clock)
    assert isinstance(digest, FollowupDigest)
    assert digest.closed >= 1
    assert digest.escalated >= 1
    assert digest.still_open >= 1


@pytest.mark.unit
def test_api_followup_routes(client: TestClient, test_env: dict[str, Any]) -> None:
    """REST API: trigger sweep, fetch status and daily digest."""
    # 1. GET status
    resp = client.get("/api/v1/staff/followup/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "ok" in data
    assert "sweep_interval_seconds" in data

    # 2. POST sweep
    resp2 = client.post("/api/v1/staff/followup/sweep")
    assert resp2.status_code == 200
    res_data = resp2.json()
    assert res_data["ok"] is True
    assert "items_scanned" in res_data

    # 3. GET digest
    resp3 = client.get("/api/v1/staff/followup/digest")
    assert resp3.status_code == 200
    digest_data = resp3.json()
    assert "closed" in digest_data
    assert "still_open" in digest_data
    assert "escalated" in digest_data
