"""Integration tests for Staff Chat Turn execution API (SC-B4, Issue #1307).

Tests:
1. POST message triggers background ChatTurnRunner, updating pending placeholder to complete.
2. Reply contract prose, metrics, and session ID are persisted in message record and thread meta.
3. Second turn resumes session ID on provider.
4. Action proposals in reply contract are created and linked in the store.
5. Provider failure results in error message with retryable remediation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.chat import ChatTurnRunner
from staff.conversations import (
    get_conversation_store,
    reset_conversation_store,
)
from staff.thread_bus import reset_thread_bus

TEST_PRINCIPAL = Principal(
    id="operator-alice",
    type="human",
    name="Alice",
    roles=["operator"],
    scopes=["staff.chat", "staff.read"],
)


@pytest.fixture(autouse=True)
def clean_conversations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    reset_thread_bus()

    app.dependency_overrides[require_principal] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_PRINCIPAL

    store = get_conversation_store()
    yield store
    app.dependency_overrides.clear()
    reset_conversation_store()
    reset_thread_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(
        app,
        headers={"X-Requested-With": "XMLHttpRequest"},
        raise_server_exceptions=False,
    )


@pytest.mark.asyncio
async def test_api_chat_turn_execution_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /threads/{id}/messages creates placeholder and executes background turn to complete."""
    mock_lines = [
        json.dumps({"type": "init", "session_id": "claude_session_api_456"}),
        json.dumps(
            {"type": "content_block_delta", "delta": {"text": "Hello Alice! Everything is operating normally."}}
        ),
    ]

    def make_proc(*a: Any, **kw: Any) -> MagicMock:
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = iter(list(mock_lines))
        proc.stderr = iter([])
        proc.poll.return_value = 0
        return proc

    with patch.object(ChatTurnRunner, "_spawn_cli_process", side_effect=make_proc):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as async_client:
            create_resp = await async_client.post(
                "/api/v1/staff/threads",
                json={"title": "Chat API Test", "role": "barb"},
            )
            assert create_resp.status_code == 201
            tid = create_resp.json()["id"]

            resp = await async_client.post(
                f"/api/v1/staff/threads/{tid}/messages",
                headers={"Idempotency-Key": "msg-key-api-1"},
                json={"body": "How are the runners?"},
            )
            assert resp.status_code == 202, resp.text
            data = resp.json()
            placeholder_id = data["reply_placeholder"]["id"]

            store = get_conversation_store()

            # Await background execution
            for _ in range(20):
                completed = store.get_message(placeholder_id)
                if completed and completed.delivery == "complete":
                    break
                await asyncio.sleep(0.05)

            assert completed is not None
            assert completed.delivery == "complete"
            assert "Hello Alice! Everything is operating normally." in completed.body_md
            assert completed.meta.get("session_id") == "claude_session_api_456"
            assert "metrics" in completed.meta

            th = store.get_thread(tid)
            assert th is not None
            assert th.meta.get("provider_sessions", {}).get("claude") == "claude_session_api_456"


@pytest.mark.asyncio
async def test_api_chat_turn_multi_turn_session_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second chat turn passes --resume <session_id> to provider adapter."""
    captured_cmds: list[list[str]] = []

    def make_proc(*a: Any, **kw: Any) -> MagicMock:
        cmd = kw.get("cmd") or a[0]
        captured_cmds.append(cmd)
        proc = MagicMock()
        proc.returncode = 0
        if len(captured_cmds) == 1:
            lines = [
                json.dumps({"type": "init", "session_id": "sess_resume_test_789"}),
                json.dumps({"type": "content_block_delta", "delta": {"text": "Turn 1 answer"}}),
            ]
        else:
            lines = [
                json.dumps({"type": "init", "session_id": "sess_resume_test_789"}),
                json.dumps({"type": "content_block_delta", "delta": {"text": "Turn 2 answer with resumed context"}}),
            ]
        proc.stdout = iter(lines)
        proc.stderr = iter([])
        proc.poll.return_value = 0
        return proc

    with patch.object(ChatTurnRunner, "_spawn_cli_process", side_effect=make_proc):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as async_client:
            create_resp = await async_client.post(
                "/api/v1/staff/threads",
                json={"title": "Multi-turn Test", "role": "barb"},
            )
            tid = create_resp.json()["id"]
            store = get_conversation_store()

            # Turn 1
            resp1 = await async_client.post(
                f"/api/v1/staff/threads/{tid}/messages",
                headers={"Idempotency-Key": "multi-turn-k1"},
                json={"body": "Hello turn 1"},
            )
            p1_id = resp1.json()["reply_placeholder"]["id"]
            for _ in range(20):
                m1 = store.get_message(p1_id)
                if m1 and m1.delivery == "complete":
                    break
                await asyncio.sleep(0.05)

            assert m1 is not None and m1.delivery == "complete"
            assert "--resume" not in captured_cmds[0]

            # Turn 2
            resp2 = await async_client.post(
                f"/api/v1/staff/threads/{tid}/messages",
                headers={"Idempotency-Key": "multi-turn-k2"},
                json={"body": "Hello turn 2"},
            )
            p2_id = resp2.json()["reply_placeholder"]["id"]
            for _ in range(20):
                m2 = store.get_message(p2_id)
                if m2 and m2.delivery == "complete":
                    break
                await asyncio.sleep(0.05)

            assert m2 is not None and m2.delivery == "complete"
            assert "--resume" in captured_cmds[1]
            assert "sess_resume_test_789" in captured_cmds[1]


@pytest.mark.asyncio
async def test_api_chat_turn_action_proposals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Action proposals in reply contract are parsed and saved to proposal store."""
    reply_payload = (
        "I will restart runner-1 for you.\n\n"
        "```staff-actions\n"
        '[{"action": "runner.restart", "params": {"runner": "runner-1"}, "reason": "stalled"}]\n'
        "```\n"
    )

    def make_proc(*a: Any, **kw: Any) -> MagicMock:
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = iter([json.dumps({"type": "text", "text": reply_payload})])
        proc.stderr = iter([])
        proc.poll.return_value = 0
        return proc

    with patch.object(ChatTurnRunner, "_spawn_cli_process", side_effect=make_proc):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as async_client:
            create_resp = await async_client.post(
                "/api/v1/staff/threads",
                json={"title": "Action Test", "role": "maintenance"},
            )
            tid = create_resp.json()["id"]
            store = get_conversation_store()

            resp = await async_client.post(
                f"/api/v1/staff/threads/{tid}/messages",
                headers={"Idempotency-Key": "action-msg-k1"},
                json={"body": "Restart runner 1"},
            )
            p_id = resp.json()["reply_placeholder"]["id"]
            for _ in range(20):
                m = store.get_message(p_id)
                if m and m.delivery == "complete":
                    break
                await asyncio.sleep(0.05)

            assert m is not None and m.delivery == "complete"
            proposals = store.list_proposals(thread_id=tid)
            assert len(proposals) == 1
            assert proposals[0].action == "runner.restart"
            assert proposals[0].params == {"runner": "runner-1"}
            # #1547: the proposal is its own ActionCard message in the thread.
            card_msg = store.get_message(proposals[0].message_id)
            assert card_msg is not None and card_msg.kind == "action_proposal"
            assert card_msg.meta["proposal"]["id"] == proposals[0].id
            assert card_msg.meta["proposal"]["status"] == "pending"
            assert card_msg.body_md == "stalled"


@pytest.mark.asyncio
async def test_api_chat_turn_failure_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider failure produces kind='error' message with classified remediation."""

    def make_proc(*a: Any, **kw: Any) -> MagicMock:
        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = iter([])
        proc.stderr = iter(["Fatal error: Authentication failed, invalid API key"])
        proc.poll.return_value = 1
        return proc

    with patch.object(ChatTurnRunner, "_spawn_cli_process", side_effect=make_proc):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as async_client:
            create_resp = await async_client.post(
                "/api/v1/staff/threads",
                json={"title": "Failure Test", "role": "barb"},
            )
            tid = create_resp.json()["id"]
            store = get_conversation_store()

            resp = await async_client.post(
                f"/api/v1/staff/threads/{tid}/messages",
                headers={"Idempotency-Key": "fail-msg-k1"},
                json={"body": "Trigger failure"},
            )
            p_id = resp.json()["reply_placeholder"]["id"]
            for _ in range(20):
                m = store.get_message(p_id)
                if m and m.delivery == "failed":
                    break
                await asyncio.sleep(0.05)

            assert m is not None
            assert m.kind == "error"
            assert m.delivery == "failed"
            assert "actions" in m.meta
            assert any(act.get("name") == "retry" for act in m.meta.get("actions", []))
