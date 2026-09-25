"""API tests for staff group threads and Board Deliberation (SC-B9, Issue #1339).

Covers:
- GET /api/v1/staff/groups (list groups)
- GET /api/v1/staff/groups/{id} (group details with seats)
- GET /api/v1/staff/groups/{id}/cost-estimate (pre-send cost calculation)
- POST /api/v1/staff/threads (create group thread with kind="group" or role="board")
- POST /api/v1/staff/threads/{id}/messages (cost guard threshold enforcement, bypass with confirm_cost)
- End-to-end group turn execution: placeholder created, background coordinator runs,
  collates seat replies, writes consensus summary, generates "board.propose" action proposal,
  and executing the proposal creates a Board Proposal work item.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.conversations import (
    get_conversation_store,
    reset_conversation_store,
)
from staff.groups import SeatReply, SeatSpec, reset_group_runner_override, set_group_runner_override
from staff.thread_bus import reset_thread_bus

TEST_OPERATOR = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator"],
    scopes=["staff.chat", "staff.read", "staff.dispatch", "staff.approve", "board.proposals.write"],
)


@pytest.fixture(autouse=True)
def clean_conversations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    reset_thread_bus()
    reset_group_runner_override()

    app.dependency_overrides[require_principal] = lambda: TEST_OPERATOR
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_OPERATOR
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_OPERATOR
    app.dependency_overrides[require_scope("staff.dispatch")] = lambda: TEST_OPERATOR
    app.dependency_overrides[require_scope("staff.approve")] = lambda: TEST_OPERATOR
    app.dependency_overrides[require_scope("board.proposals.write")] = lambda: TEST_OPERATOR

    store = get_conversation_store()
    yield store
    app.dependency_overrides.clear()
    reset_conversation_store()
    reset_thread_bus()
    reset_group_runner_override()


@pytest.fixture
def client() -> TestClient:
    return TestClient(
        app,
        headers={"X-Requested-With": "XMLHttpRequest"},
        raise_server_exceptions=False,
    )


# ── 1. GROUP LIST & DETAIL ENDPOINTS ─────────────────────────────────────────


def test_list_groups_api(client: TestClient) -> None:
    """GET /api/v1/staff/groups returns registered groups including board."""
    resp = client.get("/api/v1/staff/groups")
    assert resp.status_code == 200
    data = resp.json()
    assert "groups" in data
    board = next((g for g in data["groups"] if g["id"] == "board"), None)
    assert board is not None
    assert board["name"] == "Board of Directors" or "Board" in board["name"]
    assert board["coordinator"] == "board-secretary"
    assert len(board["seats"]) == 4


def test_get_group_detail_api(client: TestClient) -> None:
    """GET /api/v1/staff/groups/board returns detailed seat specifications."""
    resp = client.get("/api/v1/staff/groups/board")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "board"
    assert data["coordinator"] == "board-secretary"
    assert len(data["seats"]) == 4
    seat_names = [s["name"] for s in data["seats"]]
    assert "alpha" in seat_names


def test_get_group_not_found(client: TestClient) -> None:
    """GET /api/v1/staff/groups/unknown returns 404."""
    resp = client.get("/api/v1/staff/groups/non-existent-group")
    assert resp.status_code == 404


# ── 2. COST ESTIMATE ENDPOINT ────────────────────────────────────────────────


def test_group_cost_estimate_api(client: TestClient) -> None:
    """GET /api/v1/staff/groups/board/cost-estimate returns total and seat breakdown."""
    resp = client.get("/api/v1/staff/groups/board/cost-estimate?prompt=Review%20fleet%20policy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["group_id"] == "board"
    assert data["total_cost_usd"] > 0.0
    assert len(data["cost_per_seat"]) == 4
    assert "exceeds_threshold" in data


# ── 3. CREATE GROUP THREAD ───────────────────────────────────────────────────


def test_create_group_thread(client: TestClient) -> None:
    """POST /api/v1/staff/threads with role='board' or kind='group' creates group thread."""
    resp = client.post(
        "/api/v1/staff/threads",
        json={"role": "board", "title": "Board discussion on WebGPU"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["kind"] == "group"
    assert data["meta"]["group"] == "board"
    assert data["meta"]["coordinator"] == "board-secretary"
    assert "board-secretary" in data["participants"]
    assert "alpha" in data["participants"]


# ── 4. COST GUARD ON POST MESSAGE ────────────────────────────────────────────


def test_post_message_trips_cost_guard_without_confirmation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Posting message in group thread when cost exceeds threshold without confirmation returns 400."""
    monkeypatch.setenv("GROUP_COST_GUARD_THRESHOLD_USD", "0.0001")

    # 1. Create group thread
    t_resp = client.post("/api/v1/staff/threads", json={"role": "board", "title": "Guard test"})
    thread_id = t_resp.json()["id"]

    # 2. Post message without confirm_cost
    m_resp = client.post(
        f"/api/v1/staff/threads/{thread_id}/messages",
        headers={"Idempotency-Key": "group-msg-1"},
        json={"body": "Expensive query to all seats"},
    )
    assert m_resp.status_code == 400
    err = m_resp.json()
    err_dict = err.get("error") or err.get("detail") or {}
    assert err_dict.get("code") == "group_cost_guard_threshold_exceeded"
    assert "confirm_cost" in err_dict.get("message", "").lower()


def test_post_message_succeeds_with_confirm_cost(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Posting message with confirm_cost=True succeeds even if above threshold."""
    monkeypatch.setenv("GROUP_COST_GUARD_THRESHOLD_USD", "0.0001")

    # Override fake seat runner to return instantly
    async def fake_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        return SeatReply(seat_name=seat.name, status="ok", text=f"{seat.name} perspective: approved")

    set_group_runner_override(fake_runner)

    t_resp = client.post("/api/v1/staff/threads", json={"role": "board", "title": "Guard bypass"})
    thread_id = t_resp.json()["id"]

    m_resp = client.post(
        f"/api/v1/staff/threads/{thread_id}/messages",
        headers={"Idempotency-Key": "group-msg-2"},
        json={"body": "Expensive query with confirmation", "meta": {"confirm_cost": True}},
    )
    assert m_resp.status_code == 202
    assert m_resp.json()["reply_placeholder"]["delivery"] == "pending"


# ── 5. END-TO-END GROUP TURN & MAKE-A-PROPOSAL ACTION ─────────────────────────


@pytest.mark.asyncio
async def test_group_turn_e2e_and_proposal_creation(client: TestClient) -> None:
    """Group turn collates seat replies into consensus summary and proposes formal Board Proposal."""

    async def fake_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        return SeatReply(
            seat_name=seat.name,
            status="ok",
            text=f"Seat {seat.name.title()} recommendations for {prompt}.",
            cost_usd=0.005,
        )

    set_group_runner_override(fake_runner)

    # 1. Create group thread
    t_resp = client.post(
        "/api/v1/staff/threads",
        json={"role": "board", "title": "WebGPU adoption proposal discussion"},
    )
    assert t_resp.status_code == 201
    thread_id = t_resp.json()["id"]

    # 2. Post question to the Board
    m_resp = client.post(
        f"/api/v1/staff/threads/{thread_id}/messages",
        headers={"Idempotency-Key": "group-msg-3"},
        json={"body": "Should we adopt WebGPU for 3D trajectory rendering across all repos?"},
    )
    assert m_resp.status_code == 202
    placeholder_id = m_resp.json()["reply_placeholder"]["id"]

    # Wait briefly for background group turn task to finish
    store = get_conversation_store()
    for _ in range(30):
        await asyncio.sleep(0.1)
        rec = store.get_message(placeholder_id)
        if rec and rec.delivery == "complete":
            break

    completed = store.get_message(placeholder_id)
    assert completed is not None
    assert completed.delivery == "complete"
    assert "Board Deliberation & Consensus Summary" in completed.body_md
    assert "Quorum:" in completed.body_md
    assert "4/4 seats answered" in completed.body_md
    assert "Seat Replies" in completed.body_md
    assert completed.meta.get("is_group_turn") is True

    # 3. Check that "board.propose" action proposal was generated
    proposals = store.list_proposals(thread_id=thread_id)
    assert len(proposals) >= 1
    board_prop = next((p for p in proposals if p.action == "board.propose"), None)
    assert board_prop is not None
    assert board_prop.state == "proposed"
    assert "WebGPU" in board_prop.params.get("title", "")

    # 4. Approve and execute proposal ("make this a proposal")
    decide_resp = client.post(
        f"/api/v1/staff/proposals/{board_prop.id}/decide",
        json={"decision": "approved", "reason": "Approved by owner"},
    )
    assert decide_resp.status_code == 200

    exec_resp = client.post(f"/api/v1/staff/proposals/{board_prop.id}/execute")
    assert exec_resp.status_code == 200
    data = exec_resp.json()
    assert data["state"] == "done"
    assert data["execution_result"]["success"] is True
    assert data["execution_result"]["result"]["proposal_id"] is not None
