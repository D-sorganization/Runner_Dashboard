"""API tests for the ``board.convene`` action (Runner_Dashboard#1762).

Covers:
- The action requires owner/staff.approve approval before it executes (MEDIUM risk,
  never auto-executed).
- Approving and executing creates a Board group thread and starts one group turn
  (stub seat runner), returning {thread_id, title}.
- Missing 'question' is rejected as invalid_params.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.actions import ACTION_REGISTRY, ActionRiskClass, can_auto_execute
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.groups import SeatReply, SeatSpec, reset_group_runner_override, set_group_runner_override
from staff.thread_bus import reset_thread_bus

TEST_OPERATOR = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator"],
    scopes=["staff.chat", "staff.read", "staff.dispatch", "staff.approve", "board.proposals.write"],
)


async def _stub_seat_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
    """Fast default seat runner so 'take PR #N to the Board' messages never spawn a real CLI."""
    return SeatReply(seat_name=seat.name, status="ok", text=f"{seat.name} stub reply to: {prompt}")


@pytest.fixture(autouse=True)
def clean_conversations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    reset_thread_bus()
    reset_group_runner_override()
    set_group_runner_override(_stub_seat_runner)

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


def test_board_convene_is_registered_as_medium_risk_and_not_auto_executable() -> None:
    """board.convene needs owner approval: MEDIUM risk, never in the auto-execute set."""
    action = ACTION_REGISTRY.get("board.convene")
    assert action is not None
    assert action.risk_class == ActionRiskClass.MEDIUM
    assert can_auto_execute(action, "barb", None) is False


def test_board_convene_requires_decision_before_execution(client: TestClient) -> None:
    """Executing a freshly-proposed board.convene without a decision is refused."""
    thread_resp = client.post("/api/v1/staff/threads", json={"role": "board-secretary", "title": "Barb chat"})
    thread_id = thread_resp.json()["id"]
    msg_resp = client.post(
        f"/api/v1/staff/threads/{thread_id}/messages",
        headers={"Idempotency-Key": "convene-msg-1"},
        json={"body": "Take PR #11080 to the Board"},
    )
    assert msg_resp.status_code in (200, 202)
    message_id = msg_resp.json()["message"]["id"]

    prop_resp = client.post(
        "/api/v1/staff/proposals",
        json={
            "message_id": message_id,
            "thread_id": thread_id,
            "action": "board.convene",
            "params": {"question": "Should we adopt WebGPU?"},
        },
    )
    assert prop_resp.status_code == 200
    proposal_id = prop_resp.json()["id"]
    assert prop_resp.json()["risk"] == "medium"

    exec_resp = client.post(f"/api/v1/staff/proposals/{proposal_id}/execute")
    assert exec_resp.status_code == 409


@pytest.mark.asyncio
async def test_board_convene_approved_creates_thread_and_starts_group_turn(client: TestClient) -> None:
    """Approving + executing board.convene creates a Board thread and fans out one group turn."""

    async def fake_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        return SeatReply(seat_name=seat.name, status="ok", text=f"{seat.name} view on: {prompt}")

    set_group_runner_override(fake_runner)

    thread_resp = client.post("/api/v1/staff/threads", json={"role": "board-secretary", "title": "Barb chat"})
    origin_thread_id = thread_resp.json()["id"]
    msg_resp = client.post(
        f"/api/v1/staff/threads/{origin_thread_id}/messages",
        headers={"Idempotency-Key": "convene-msg-2"},
        json={"body": "Take PR #11080 to the Board"},
    )
    message_id = msg_resp.json()["message"]["id"]

    prop_resp = client.post(
        "/api/v1/staff/proposals",
        json={
            "message_id": message_id,
            "thread_id": origin_thread_id,
            "action": "board.convene",
            "params": {"question": "Should we adopt WebGPU?", "title": "WebGPU Deliberation"},
        },
    )
    proposal_id = prop_resp.json()["id"]

    decide_resp = client.post(
        f"/api/v1/staff/proposals/{proposal_id}/decide",
        json={"decision": "approved", "reason": "owner approved", "execute": True},
    )
    assert decide_resp.status_code == 200
    data = decide_resp.json()
    assert data["state"] == "done"
    exec_result = data["execution_result"]
    assert exec_result["success"] is True
    board_thread_id = exec_result["result"]["thread_id"]
    assert exec_result["result"]["title"] == "WebGPU Deliberation"
    assert board_thread_id and board_thread_id != origin_thread_id

    store = get_conversation_store()
    board_thread = store.get_thread(board_thread_id)
    assert board_thread is not None
    assert board_thread.kind == "group"
    assert board_thread.meta.get("group") == "board"
    assert "board-secretary" in board_thread.participants
    assert "alpha" in board_thread.participants

    # The first group turn (the question) was started in the background.
    messages = store.list_messages(board_thread_id)
    assert any("Should we adopt WebGPU?" in (m.body_md or "") for m in messages)
    placeholder = next(m for m in messages if m.meta.get("is_group_turn") and m.author == "board-secretary")

    for _ in range(30):
        await asyncio.sleep(0.1)
        rec = store.get_message(placeholder.id)
        if rec and rec.delivery == "complete":
            break

    completed = store.get_message(placeholder.id)
    assert completed is not None
    assert completed.delivery == "complete"
    assert "### Board Deliberation" in completed.body_md


def test_board_convene_missing_question_is_invalid_params(client: TestClient) -> None:
    thread_resp = client.post("/api/v1/staff/threads", json={"role": "board-secretary", "title": "Barb chat"})
    thread_id = thread_resp.json()["id"]
    msg_resp = client.post(
        f"/api/v1/staff/threads/{thread_id}/messages",
        headers={"Idempotency-Key": "convene-msg-3"},
        json={"body": "convene the board please"},
    )
    message_id = msg_resp.json()["message"]["id"]

    prop_resp = client.post(
        "/api/v1/staff/proposals",
        json={
            "message_id": message_id,
            "thread_id": thread_id,
            "action": "board.convene",
            "params": {},
        },
    )
    proposal_id = prop_resp.json()["id"]

    decide_resp = client.post(
        f"/api/v1/staff/proposals/{proposal_id}/decide",
        json={"decision": "approved", "reason": "owner approved", "execute": True},
    )
    assert decide_resp.status_code == 200
    data = decide_resp.json()
    assert data["state"] == "failed"
    assert data["execution_result"]["failure_class"] == "invalid_params"
