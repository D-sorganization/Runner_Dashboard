"""Regression coverage for persistent, epoch-fenced Barb CLI turns (#2253)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from staff.chat_context_budget import cap_chat_context
from staff.chat_streaming import stream_turn_output
from staff.conversations import ConversationStore, StaleThreadEpochError
from staff.standards_registry import estimate_tokens
from staff.thread_bus import ThreadEventBus


def test_thread_epoch_persists_and_advances_atomically_across_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "staff.sqlite3"
    store = ConversationStore(db_path)
    th = store.create_thread("Barb", participants=["owner", "barb"])
    assert th.epoch == 0 and store.advance_thread_epoch(th.id, "seat_handoff") == 1
    store._conn.close()

    restarted = ConversationStore(db_path)
    reloaded = restarted.get_thread(th.id)
    assert reloaded is not None and reloaded.epoch == 1
    assert restarted.advance_thread_epoch(th.id, "seat_failover") == 2
    restarted._conn.close()


def test_stale_turn_cannot_append_or_update_message_after_handoff(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "staff.sqlite3")
    th = store.create_thread("Barb", participants=["owner", "barb"])
    msg = store.add_message(th.id, author_kind="role", author="barb", delivery="pending")
    stale_epoch = th.epoch
    store.advance_thread_epoch(th.id, "seat_handoff")

    with pytest.raises(StaleThreadEpochError, match="epoch"):
        store.add_message(th.id, author_kind="role", author="barb", body_md="late", turn_epoch=stale_epoch)
    assert store.update_message(msg.id, turn_epoch=stale_epoch, body_md="late") is None
    assert store.get_message(msg.id).body_md == ""
    store._conn.close()


def test_context_budget_keeps_knowledge_and_bounded_handoff_digest() -> None:
    knowledge, digest, standards = (
        "## Knowledge\n" + ("e " * 400),
        "## Handoff digest\n\n" + ("d " * 240),
        "## Fleet standards Tier 0\n\n" + ("s " * 500),
    )
    packed = cap_chat_context((standards, knowledge, digest))
    assert packed[0] and "Fleet standards Tier 0" in packed[0]
    assert packed[1] and "Knowledge" in packed[1]
    assert packed[2] and "Handoff digest" in packed[2]
    assert estimate_tokens("\n\n".join(i for i in packed if i)) <= 2200 and estimate_tokens(packed[2]) <= 600


def test_proposal_approval_is_one_shot_and_stale_epoch_is_rejected(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "staff.sqlite3")
    th = store.create_thread("Barb", participants=["owner", "barb"])
    msg = store.add_message(th.id, author_kind="role", author="barb")
    prop = store.create_proposal(message_id=msg.id, thread_id=th.id, action="staff.dispatch")
    assert prop.epoch == 0
    store.decide_proposal(prop.id, "approved", decided_by="owner")
    with pytest.raises(ValueError, match="Cannot decide proposal"):
        store.decide_proposal(prop.id, "approved", decided_by="owner")

    msg2 = store.add_message(th.id, author_kind="role", author="barb")
    stale = store.create_proposal(message_id=msg2.id, thread_id=th.id, action="staff.dispatch")
    store.advance_thread_epoch(th.id, "seat_handoff")
    with pytest.raises(ValueError, match="stale thread epoch"):
        store.decide_proposal(stale.id, "approved", decided_by="owner")
    store._conn.close()


def test_streamed_token_carries_thread_and_epoch_and_stale_stream_is_dropped() -> None:
    class Reader:
        returncode = 0
        stderr_lines: list[str] = []

        async def stream_lines(self):
            yield "live token"

    adapter = SimpleNamespace(provider_id="test-seat", json_lines=False, parse_line=lambda line: {"text": line})

    async def run() -> None:
        bus = ThreadEventBus()
        q = await bus.subscribe("th-1")
        await stream_turn_output(Reader(), adapter, bus, "th-1", "m-1", epoch=4, is_current_epoch=lambda: True)
        ev = await q.get()
        assert ev["event"] == "token"
        assert ev["data"] == {"thread_id": "th-1", "message_id": "m-1", "delta": "live token", "epoch": 4}

        dbus = ThreadEventBus()
        dq = await dbus.subscribe("th-1")
        await stream_turn_output(Reader(), adapter, dbus, "th-1", "m-1", epoch=3, is_current_epoch=lambda: False)
        assert dq.empty()

    asyncio.run(run())
