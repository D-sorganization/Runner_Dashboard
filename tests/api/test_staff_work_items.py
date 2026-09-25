"""Tests for Work-Item Ledger (SC-C3, Issue #1316).

Acceptance criteria:
- Every request Barb (or anyone) dispatches is tracked to a terminal state (done, cancelled, escalated).
- Dispatching from a thread creates a work item.
- Run events and PR outcomes update work item states automatically.
- Work items support SLA deadlines (expected_by) and overdue filtering.
- Valid state machine transitions are audited (SC-A8); invalid transitions fail visibly.
- REST API at /api/v1/staff/work-items with filters (mine, overdue, waiting_on_me, thread_id).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from routers.staff_work_items import router as work_items_router
from staff.store import reset_store
from staff.work_items import (
    InvalidStateTransitionError,
    WorkItemStore,
    get_work_item_store,
    reset_work_item_store,
)

TEST_CALLER = Principal(
    id="test-operator",
    type="human",
    name="Test Operator",
    roles=["admin"],
    scopes=["staff.read", "staff.chat", "staff.dispatch", "staff.write"],
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[WorkItemStore]:
    db = tmp_path / "test_staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    reset_store()
    reset_work_item_store()
    s = get_work_item_store(db)
    yield s
    reset_work_item_store()
    reset_store()


@pytest.fixture
def client(store: WorkItemStore) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(work_items_router, prefix="/api/v1/staff")

    app.dependency_overrides[require_principal] = lambda: TEST_CALLER
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_CALLER
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_CALLER
    app.dependency_overrides[require_scope("staff.dispatch")] = lambda: TEST_CALLER
    app.dependency_overrides[require_scope("staff.write")] = lambda: TEST_CALLER

    with TestClient(app) as tc:
        yield tc

    app.dependency_overrides.clear()


@pytest.mark.unit
def test_work_item_creation_and_defaults(store: WorkItemStore) -> None:
    now = datetime.now(UTC)
    expected_by = (now + timedelta(hours=2)).isoformat().replace("+00:00", "Z")

    item = store.create_work_item(
        title="Refactor auth middleware",
        requested_by="operator-1",
        thread_id="th-123",
        owner_role="librarian",
        expected_by=expected_by,
        links={"issues": ["#1316"], "runs": ["run-001"]},
    )
    assert item.id.startswith("wi-")
    assert item.title == "Refactor auth middleware"
    assert item.requested_by == "operator-1"
    assert item.thread_id == "th-123"
    assert item.owner_role == "librarian"
    assert item.state == "open"
    assert item.expected_by == expected_by
    assert item.links["issues"] == ["#1316"]
    assert item.links["runs"] == ["run-001"]

    fetched = store.get_work_item(item.id)
    assert fetched is not None
    assert fetched.id == item.id
    assert fetched.title == item.title
    assert fetched.links["runs"] == ["run-001"]


@pytest.mark.unit
def test_state_machine_valid_transitions(store: WorkItemStore) -> None:
    item = store.create_work_item(
        title="Implement feature",
        requested_by="operator-1",
    )
    assert item.state == "open"

    # open -> in_progress
    step1 = store.transition_state(item.id, "in_progress", actor="barb", reason="Dispatched run")
    assert step1.state == "in_progress"

    # in_progress -> waiting_on_user
    step2 = store.transition_state(item.id, "waiting_on_user", actor="claude", reason="Needs input on schema")
    assert step2.state == "waiting_on_user"

    # waiting_on_user -> in_progress
    step3 = store.transition_state(item.id, "in_progress", actor="operator-1", reason="User replied")
    assert step3.state == "in_progress"

    # in_progress -> waiting_on_ci
    step4 = store.transition_state(item.id, "waiting_on_ci", actor="barb", reason="PR #100 opened")
    assert step4.state == "waiting_on_ci"

    # waiting_on_ci -> done
    step5 = store.transition_state(item.id, "done", actor="barb", reason="PR #100 merged")
    assert step5.state == "done"


@pytest.mark.unit
def test_state_machine_rejects_invalid_transitions(store: WorkItemStore) -> None:
    item = store.create_work_item(
        title="Strict lifecycle item",
        requested_by="operator-1",
    )
    assert item.state == "open"

    # open -> done directly is invalid without progress
    with pytest.raises(InvalidStateTransitionError):
        store.transition_state(item.id, "done", actor="operator-1")

    # Move to cancelled (terminal)
    cancelled = store.transition_state(item.id, "cancelled", actor="operator-1", reason="Aborted")
    assert cancelled.state == "cancelled"

    # Transitioning out of cancelled is invalid
    with pytest.raises(InvalidStateTransitionError):
        store.transition_state(item.id, "in_progress", actor="operator-1")


@pytest.mark.unit
def test_sla_and_overdue_detection(store: WorkItemStore) -> None:
    now = datetime.now(UTC)
    past_sla = (now - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
    future_sla = (now + timedelta(hours=2)).isoformat().replace("+00:00", "Z")

    item_overdue = store.create_work_item(
        title="Past SLA task",
        requested_by="operator-1",
        expected_by=past_sla,
    )
    store.transition_state(item_overdue.id, "in_progress", actor="barb")

    item_on_time = store.create_work_item(
        title="Future SLA task",
        requested_by="operator-1",
        expected_by=future_sla,
    )
    store.transition_state(item_on_time.id, "in_progress", actor="barb")

    # Completed items are never overdue
    item_completed_late = store.create_work_item(
        title="Completed late task",
        requested_by="operator-1",
        expected_by=past_sla,
    )
    store.transition_state(item_completed_late.id, "in_progress", actor="barb")
    store.transition_state(item_completed_late.id, "done", actor="barb")

    overdue_items = store.list_work_items(overdue=True)
    overdue_ids = [w.id for w in overdue_items]
    assert item_overdue.id in overdue_ids
    assert item_on_time.id not in overdue_ids
    assert item_completed_late.id not in overdue_ids


@pytest.mark.unit
def test_links_and_progress_recording(store: WorkItemStore) -> None:
    item = store.create_work_item(
        title="Track progress",
        requested_by="operator-1",
    )
    updated = store.add_link(item.id, "runs", "run-abc-123")
    assert "run-abc-123" in updated.links["runs"]

    updated = store.add_link(item.id, "prs", "https://github.com/D-sorganization/Tools/pull/42")
    assert "https://github.com/D-sorganization/Tools/pull/42" in updated.links["prs"]

    # Deduplication
    updated = store.add_link(item.id, "runs", "run-abc-123")
    assert updated.links["runs"] == ["run-abc-123"]


@pytest.mark.unit
def test_api_create_and_get_work_item(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/staff/work-items",
        json={
            "title": "Build UI component",
            "thread_id": "th-xyz",
            "owner_role": "cartographer",
            "expected_by": "2026-09-25T12:00:00Z",
            "links": {"issues": ["#42"]},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Build UI component"
    assert data["thread_id"] == "th-xyz"
    assert data["owner_role"] == "cartographer"
    assert data["state"] == "open"
    item_id = data["id"]

    get_resp = client.get(f"/api/v1/staff/work-items/{item_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == item_id


@pytest.mark.unit
def test_api_patch_work_item_transition(client: TestClient) -> None:
    create_resp = client.post(
        "/api/v1/staff/work-items",
        json={"title": "Test patch transition"},
    )
    item_id = create_resp.json()["id"]

    patch_resp = client.patch(
        f"/api/v1/staff/work-items/{item_id}",
        json={"state": "in_progress", "reason": "Worker assigned"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["state"] == "in_progress"

    # Invalid transition returns 400
    bad_patch = client.patch(
        f"/api/v1/staff/work-items/{item_id}",
        json={"state": "open", "reason": "Cannot reopen to open"},
    )
    assert bad_patch.status_code == 400
    assert "invalid_transition" in bad_patch.json().get("detail", {}).get("code", "")


@pytest.mark.unit
def test_api_list_filters(client: TestClient) -> None:
    client.post(
        "/api/v1/staff/work-items",
        json={"title": "Item 1", "thread_id": "th-1"},
    )
    client.post(
        "/api/v1/staff/work-items",
        json={"title": "Item 2", "thread_id": "th-2"},
    )

    resp_all = client.get("/api/v1/staff/work-items")
    assert resp_all.status_code == 200
    assert len(resp_all.json()["items"]) >= 2

    resp_filtered = client.get("/api/v1/staff/work-items?thread_id=th-1")
    assert resp_filtered.status_code == 200
    items = resp_filtered.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "Item 1"


@pytest.mark.unit
def test_run_status_changes_update_work_item_lifecycle(store: WorkItemStore) -> None:
    wi = store.create_work_item(title="Tracked run task", requested_by="operator-1")
    assert wi.state == "open"

    from staff.run_link import handle_run_status_change
    from staff.store import RunRecord

    run = RunRecord(
        id="run-wi-001",
        role="ad-hoc",
        provider="claude",
        model=None,
        machine="DeskComputer",
        repo="UpstreamDrift",
        target_kind="prompt",
        target_ref="",
        prompt="Do work",
        work_item_id=wi.id,
    )

    # 1. Run starts -> work item moves to in_progress
    handle_run_status_change(run, "running")
    wi_updated = store.get_work_item(wi.id)
    assert wi_updated is not None
    assert wi_updated.state == "in_progress"
    assert "run-wi-001" in wi_updated.links["runs"]

    # 2. Run needs_input -> work item moves to waiting_on_user
    handle_run_status_change(run, "needs_input", question="What branch?")
    wi_updated = store.get_work_item(wi.id)
    assert wi_updated is not None
    assert wi_updated.state == "waiting_on_user"

    # 3. Continuation run runs -> in_progress
    handle_run_status_change(run, "running")
    wi_updated = store.get_work_item(wi.id)
    assert wi_updated is not None
    assert wi_updated.state == "in_progress"

    # 4. Run succeeds -> done
    handle_run_status_change(run, "succeeded", summary="Resolved issue #42")
    wi_updated = store.get_work_item(wi.id)
    assert wi_updated is not None
    assert wi_updated.state == "done"
