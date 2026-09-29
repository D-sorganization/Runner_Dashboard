"""POST to an auto-route thread runs confident messages as the specialist, with no Barb turn (#1567)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from identity import Principal, require_principal, require_scope
from server import app
from staff.chat import ChatTurnRunner
from staff.conversations import ConversationStore, get_conversation_store, reset_conversation_store
from staff.thread_bus import reset_thread_bus

TEST_PRINCIPAL = Principal(
    id="operator-alice",
    type="human",
    name="Alice",
    roles=["operator"],
    scopes=["staff.chat", "staff.read"],
)
ROSTER = {"barb": MagicMock(), "maintenance": MagicMock(), "issue-remediator": MagicMock()}


@pytest.fixture(autouse=True)
def clean_conversations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    monkeypatch.setattr("staff.chat_preroute.load_roles", lambda: ROSTER)
    reset_conversation_store()
    reset_thread_bus()
    app.dependency_overrides[require_principal] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_PRINCIPAL
    yield get_conversation_store()
    app.dependency_overrides.clear()
    reset_conversation_store()
    reset_thread_bus()


def _make_proc(*_a: Any, **_kw: Any) -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = iter([json.dumps({"type": "content_block_delta", "delta": {"text": "Queue looks healthy."}})])
    proc.stderr = iter([])
    proc.poll.return_value = 0
    return proc


async def _post_to_auto_thread(text: str) -> dict[str, Any]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Requested-With": "XMLHttpRequest"},
    ) as client:
        created = await client.post("/api/v1/staff/threads", json={"title": "Ask Barb", "kind": "auto"})
        assert created.status_code == 201, created.text
        resp = await client.post(
            f"/api/v1/staff/threads/{created.json()['id']}/messages",
            headers={"Idempotency-Key": f"key-{text}"},
            json={"body": text},
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()
        data["auto_thread_id"] = created.json()["id"]
        return data


async def _post_message(thread_id: str, text: str) -> dict[str, Any]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Requested-With": "XMLHttpRequest"},
    ) as client:
        resp = await client.post(
            f"/api/v1/staff/threads/{thread_id}/messages",
            headers={"Idempotency-Key": f"key-{thread_id}-{text}"},
            json={"body": text},
        )
        assert resp.status_code == 202, resp.text
        return resp.json()


async def _await_complete(store: ConversationStore, message_id: str) -> Any:
    for _ in range(40):
        msg = store.get_message(message_id)
        if msg and msg.delivery == "complete":
            return msg
        await asyncio.sleep(0.05)
    return store.get_message(message_id)


def _replies(store: ConversationStore, thread_id: str) -> list[Any]:
    return [m for m in store.list_messages(thread_id) if m.author_kind == "role" and m.kind == "text"]


@pytest.mark.asyncio
async def test_a_confident_message_runs_as_the_specialist_with_no_barb_turn() -> None:
    store = get_conversation_store()
    with patch.object(ChatTurnRunner, "_spawn_cli_process", side_effect=_make_proc):
        data = await _post_to_auto_thread("analyse the queue backlog")
        reply = await _await_complete(store, data["reply_placeholder"]["id"])

    auto_id = data["auto_thread_id"]
    assert reply.author == "maintenance"
    assert reply.thread_id != auto_id
    assert "Queue looks healthy." in reply.body_md
    assert _replies(store, auto_id) == []
    cards = [m for m in store.list_messages(auto_id) if m.kind == "handoff"]
    assert len(cards) == 1
    assert cards[0].meta["reason"] == 'auto-routed: matched "analyse"'
    assert cards[0].meta["target_thread_id"] == reply.thread_id
    assert data["handoff"]["id"] == cards[0].id
    assert data["acknowledgement"]["body_md"] == "On it: routing to maintenance..."


@pytest.mark.asyncio
async def test_an_ambiguous_message_runs_as_barb() -> None:
    store = get_conversation_store()
    with patch.object(ChatTurnRunner, "_spawn_cli_process", side_effect=_make_proc):
        data = await _post_to_auto_thread("analyse issue #12 for me")
        reply = await _await_complete(store, data["reply_placeholder"]["id"])

    auto_id = data["auto_thread_id"]
    assert reply.author == "barb"
    assert reply.thread_id == auto_id
    assert reply.delivery == "complete"
    assert [m for m in store.list_messages(auto_id) if m.kind == "handoff"] == []
    assert "handoff" not in data


@pytest.mark.asyncio
async def test_a_preroute_failure_falls_back_to_barb() -> None:
    store = get_conversation_store()
    with (
        patch.object(ChatTurnRunner, "_spawn_cli_process", side_effect=_make_proc),
        patch("staff.chat_preroute.BarbRouter.execute_handoff", side_effect=RuntimeError("boom")),
    ):
        data = await _post_to_auto_thread("analyse the queue backlog")
        reply = await _await_complete(store, data["reply_placeholder"]["id"])

    auto_id = data["auto_thread_id"]
    assert reply.author == "barb"
    assert reply.thread_id == auto_id
    assert reply.delivery == "complete"
    assert "Queue looks healthy." in reply.body_md


@pytest.mark.asyncio
async def test_a_keyword_follow_up_after_barb_replies_stays_with_barb() -> None:
    """A caller follow-up in an auto thread that already holds a Barb reply is not pre-routed (#1760)."""
    store = get_conversation_store()
    with patch.object(ChatTurnRunner, "_spawn_cli_process", side_effect=_make_proc):
        data = await _post_to_auto_thread("analyse issue #12 for me")
        first_reply = await _await_complete(store, data["reply_placeholder"]["id"])
        auto_id = data["auto_thread_id"]
        assert first_reply.author == "barb"

        follow_up = await _post_message(auto_id, "analyse the queue backlog")
        second_reply = await _await_complete(store, follow_up["reply_placeholder"]["id"])

    assert second_reply.author == "barb"
    assert second_reply.thread_id == auto_id
    assert [m for m in store.list_messages(auto_id) if m.kind == "handoff"] == []
    assert "handoff" not in follow_up


@pytest.mark.asyncio
async def test_an_explicit_mention_follow_up_after_barb_replies_still_routes() -> None:
    """An explicit @mention follow-up may still pre-route even after Barb has replied (#1760)."""
    store = get_conversation_store()
    with patch.object(ChatTurnRunner, "_spawn_cli_process", side_effect=_make_proc):
        data = await _post_to_auto_thread("analyse issue #12 for me")
        first_reply = await _await_complete(store, data["reply_placeholder"]["id"])
        auto_id = data["auto_thread_id"]
        assert first_reply.author == "barb"

        follow_up = await _post_message(auto_id, "@maintenance please take this")
        second_reply = await _await_complete(store, follow_up["reply_placeholder"]["id"])

    assert second_reply.author == "maintenance"
    assert second_reply.thread_id != auto_id
    cards = [m for m in store.list_messages(auto_id) if m.kind == "handoff"]
    assert len(cards) == 1


@pytest.mark.asyncio
async def test_a_keyword_inside_a_pasted_table_row_does_not_route_a_fresh_message() -> None:
    """Keyword pre-routing ignores rule keywords pasted inside a markdown table row (#1760)."""
    store = get_conversation_store()
    text = (
        "Here is the decision table:\n\n"
        "| Proposal | Decision |\n"
        "| --- | --- |\n"
        "| Isolate Engine, Recorder, and Analysis State | Take it to the Board of Directors |\n"
    )
    with patch.object(ChatTurnRunner, "_spawn_cli_process", side_effect=_make_proc):
        data = await _post_to_auto_thread(text)
        reply = await _await_complete(store, data["reply_placeholder"]["id"])

    auto_id = data["auto_thread_id"]
    assert reply.author == "barb"
    assert reply.thread_id == auto_id
    assert [m for m in store.list_messages(auto_id) if m.kind == "handoff"] == []
    assert "handoff" not in data
