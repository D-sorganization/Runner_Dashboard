"""Tests for staff proposals API endpoints (SC-F4, Issue #1323)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.thread_bus import reset_thread_bus

TEST_APPROVER = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator"],
    scopes=["staff.chat", "staff.read", "staff.approve"],
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
