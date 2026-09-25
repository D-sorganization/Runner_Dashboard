"""Tests for staff proposals and actions API endpoints (SC-B6 #1313, SC-F4 #1323)."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.thread_bus import reset_thread_bus

UTC = getattr(_dt, "UTC", _dt.UTC)
datetime = _dt.datetime

TEST_APPROVER = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator", "owner"],
    scopes=["staff.chat", "staff.read", "staff.approve", "staff.dispatch", "admin", "owner"],
)


@pytest.fixture(autouse=True)
def clean_conversations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    reset_thread_bus()

    app.dependency_overrides[require_principal] = lambda: TEST_APPROVER
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_APPROVER
    app.dependency_overrides[require_scope("staff.approve")] = lambda: TEST_APPROVER

    store = get_conversation_store()
    yield
    app.dependency_overrides.clear()
    store.close()
    reset_conversation_store()
    reset_thread_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def test_list_and_decide_proposals_endpoints(client: TestClient) -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Test", kind="direct", participants=["barb", "user"])
    prop = store.create_proposal(
        message_id="msg_1",
        thread_id=th.id,
        action="runner.restart",
        params={"runner_name": "oglaptop"},
        risk="high",
        principal="barb",
    )

    # 1. List proposals (requires staff.read)
    res = client.get("/api/v1/staff/proposals")
    assert res.status_code == 200, res.text
    data = res.json()
    assert "items" in data
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == prop.id
    assert data["items"][0]["state"] == "proposed"

    # Filter by thread_id
    res_th = client.get(f"/api/v1/staff/proposals?thread_id={th.id}")
    assert res_th.status_code == 200
    assert len(res_th.json()["items"]) == 1

    # Filter by non-existent thread_id
    res_empty = client.get("/api/v1/staff/proposals?thread_id=nonexistent")
    assert res_empty.status_code == 200
    assert len(res_empty.json()["items"]) == 0

    # 2. Decide proposal (requires staff.approve)
    headers = {"X-Requested-With": "XMLHttpRequest"}
    # Invalid decision
    bad_decide = client.post(
        f"/api/v1/staff/proposals/{prop.id}/decide",
        json={"decision": "maybe", "reason": "not sure"},
        headers=headers,
    )
    assert bad_decide.status_code == 400

    # Valid decision: approve
    ok_decide = client.post(
        f"/api/v1/staff/proposals/{prop.id}/decide",
        json={"decision": "approved", "reason": "looks safe"},
        headers=headers,
    )
    assert ok_decide.status_code == 200, ok_decide.text
    decided_prop = ok_decide.json()
    assert decided_prop["id"] == prop.id
    assert decided_prop["state"] == "approved"
    assert decided_prop["reason"] == "looks safe"

    # Cannot decide again (must be proposed)
    dup_decide = client.post(
        f"/api/v1/staff/proposals/{prop.id}/decide",
        json={"decision": "denied", "reason": "too late"},
        headers=headers,
    )
    assert dup_decide.status_code in (400, 409)


def test_decide_nonexistent_proposal_returns_404(client: TestClient) -> None:
    res = client.post(
        "/api/v1/staff/proposals/prop_nonexistent/decide",
        json={"decision": "approved", "reason": "ok"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert res.status_code == 404


def test_list_and_get_actions_endpoints(client: TestClient) -> None:
    res = client.get("/api/v1/staff/actions")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "items" in body
    names = {a["name"] for a in body["items"]}
    assert "staff.dispatch" in names
    assert "staff.hold" in names

    # Get single action
    res_one = client.get("/api/v1/staff/actions/staff.dispatch")
    assert res_one.status_code == 200, res_one.text
    act = res_one.json()
    assert act["name"] == "staff.dispatch"
    assert act["risk_class"] == "medium"

    # 404 for unknown action
    res_unknown = client.get("/api/v1/staff/actions/unknown.nonexistent")
    assert res_unknown.status_code == 404


def test_create_and_execute_staff_dispatch_flow(client: TestClient) -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Dispatch Flow", kind="direct", participants=["barb", "user"])
    msg = store.add_message(
        thread_id=th.id,
        author_kind="role",
        author="barb",
        body_md="I suggest dispatching librarian to check catalog",
    )

    headers = {"X-Requested-With": "XMLHttpRequest"}
    # Create proposal via POST /api/v1/staff/proposals
    prop_res = client.post(
        "/api/v1/staff/proposals",
        json={
            "message_id": msg.id,
            "thread_id": th.id,
            "action": "staff.dispatch",
            "params": {"role": "librarian", "repo": "Repository_Management", "prompt": "verify issues"},
            "risk": "medium",
        },
        headers=headers,
    )
    assert prop_res.status_code == 200, prop_res.text
    prop_data = prop_res.json()
    prop_id = prop_data["id"]

    # User approves and requests execution
    decide_res = client.post(
        f"/api/v1/staff/proposals/{prop_id}/decide",
        json={"decision": "approved", "reason": "approved by operator", "execute": True},
        headers=headers,
    )
    assert decide_res.status_code == 200, decide_res.text
    decide_data = decide_res.json()
    assert decide_data["state"] == "done"
    assert "execution_result" in decide_data
    assert decide_data["execution_result"]["success"] is True

    # Verify thread contains run_card and action_result messages
    msgs = store.list_messages(th.id)
    kinds = [m.kind for m in msgs]
    assert "action_result" in kinds
    assert "run_card" in kinds


def test_execute_proposal_permission_denial(client: TestClient) -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Restricted Flow", kind="direct", participants=["bot", "user"])
    # Message by a restricted bot role that has no permission to dispatch
    msg = store.add_message(
        thread_id=th.id,
        author_kind="role",
        author="restricted_bot",
        body_md="I want to restart runner",
    )

    prop = store.create_proposal(
        message_id=msg.id,
        thread_id=th.id,
        action="maintenance.runner_stop",
        params={"runner_name": "oglaptop", "proposing_role": "restricted_bot"},
        risk="high",
        principal="restricted_bot",
    )

    headers = {"X-Requested-With": "XMLHttpRequest"}
    # Approve without execute first
    ok_decide = client.post(
        f"/api/v1/staff/proposals/{prop.id}/decide",
        json={"decision": "approved", "reason": "ok", "execute": False},
        headers=headers,
    )
    assert ok_decide.status_code == 200

    # Execute directly -> fails with 403 because role lacks permission
    exec_res = client.post(
        f"/api/v1/staff/proposals/{prop.id}/execute",
        headers=headers,
    )
    assert exec_res.status_code == 403


def test_execute_proposal_expiry_and_replay_protection(client: TestClient) -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Replay Flow", kind="direct", participants=["barb", "user"])
    prop = store.create_proposal(
        message_id="msg_r",
        thread_id=th.id,
        action="maintenance.diagnose",
        params={},
        risk="read",
        principal="barb",
    )

    # Make proposal older than 24 hours
    old_time = (datetime.now(UTC) - _dt.timedelta(hours=26)).isoformat()
    with store._lock:
        store._conn.execute("UPDATE action_proposals SET created_at = ? WHERE id = ?", (old_time, prop.id))

    headers = {"X-Requested-With": "XMLHttpRequest"}
    # Attempting to decide/execute expired proposal returns 400
    res_exp = client.post(
        f"/api/v1/staff/proposals/{prop.id}/decide",
        json={"decision": "approved", "reason": "too late", "execute": True},
        headers=headers,
    )
    assert res_exp.status_code == 400
    assert "expired" in res_exp.text.lower()
