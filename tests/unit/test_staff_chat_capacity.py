"""Unit tests for chat pool saturation, capacity handling, and Barb reservation (SC-B1-G9, Issue #1492).

Acceptance criteria:
- Unit test with pool size 1: the second concurrent turn gets chat_capacity; Barb still gets its reserved slot.
- Reply marked failed with failure_class="chat_capacity".
- System message posted (all chat slots busy, retry).
- State change audited (SC-A8).
- Concurrency limit never exceeded.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from staff.chat import (
    ChatConcurrencyPool,
    ChatTurnRunner,
)
from staff.chat_pool import (
    get_chat_pool,
    handle_capacity_exhausted,
    reset_chat_pool,
)
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
    reset_conversation_store,
)
from staff.thread_bus import get_thread_bus, reset_thread_bus


@pytest.fixture
def conv_store(tmp_path: Path) -> ConversationStore:
    reset_conversation_store()
    reset_thread_bus()
    db_file = tmp_path / "test_chat_capacity.sqlite3"
    return get_conversation_store(db_file)


# ── 1. ACCEPTANCE CRITERIA: POOL SIZE 1 & BARB RESERVATION ────────────────────


@pytest.mark.unit
def test_chat_pool_size_1_barb_reservation() -> None:
    """Pool size 1 with barb_reserved=1: Barb can acquire, non-Barb cannot."""
    pool = ChatConcurrencyPool(max_concurrency=1, barb_reserved=1)
    assert pool.barb_reserved == 1

    # Non-Barb role cannot steal Barb's reserved slot
    assert pool.try_acquire(role="night-watch") is False

    # Barb can acquire its reserved slot
    assert pool.try_acquire(role="barb") is True

    # Second concurrent turn (even Barb) cannot exceed pool size 1
    assert pool.try_acquire(role="barb") is False

    # Release Barb slot
    pool.release(role="barb")

    # Barb can acquire again
    assert pool.try_acquire(role="barb") is True
    pool.release(role="barb")


@pytest.mark.asyncio
async def test_second_concurrent_turn_gets_chat_capacity(conv_store: ConversationStore) -> None:
    """Pool size 1: second concurrent turn gets chat_capacity, failed reply, and system message."""
    pool = ChatConcurrencyPool(max_concurrency=1, barb_reserved=1)

    # First turn: Barb acquires the single slot
    assert pool.try_acquire(role="barb") is True

    # Setup second turn
    thread = conv_store.create_thread(title="Busy Thread", role="barb", created_by="alice")
    u2 = conv_store.add_message(thread.id, author_kind="user", author="alice", body_md="Second prompt")
    p2 = conv_store.add_message(
        thread.id,
        author_kind="role",
        author="barb",
        kind="text",
        body_md="",
        delivery="pending",
        meta={"in_reply_to": u2.id},
    )

    runner = ChatTurnRunner(conv_store=conv_store, pool=pool, acquire_timeout=0.01)

    with patch.object(runner, "_spawn_cli_process") as mock_spawn:
        res = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=u2.id,
            placeholder_id=p2.id,
            role_name="barb",
            provider="claude",
        )

        # Process should NOT have been spawned (never exceed pool)
        mock_spawn.assert_not_called()

        # Turn result should be failed with chat_capacity
        assert res.ok is False
        assert res.failure_class == "chat_capacity"
        assert res.retryable is True

        # Reply placeholder must be marked failed with failure_class="chat_capacity" and preserve in_reply_to
        reply_msg = conv_store.get_message(p2.id)
        assert reply_msg is not None
        assert reply_msg.delivery == "failed"
        assert reply_msg.kind == "error"
        assert reply_msg.meta.get("failure_class") == "chat_capacity"
        assert reply_msg.meta.get("retryable") is True
        assert reply_msg.meta.get("in_reply_to") == u2.id

        # System message posted to thread (all chat slots busy, retry)
        messages = conv_store.list_messages(thread.id)
        system_msgs = [
            m for m in messages if m.author_kind == "system" and m.meta.get("failure_class") == "chat_capacity"
        ]
        assert len(system_msgs) == 1
        sys_msg = system_msgs[0]
        assert "busy" in sys_msg.body_md.lower()
        assert sys_msg.delivery == "complete"
        assert sys_msg.meta.get("in_reply_to") == u2.id

        # User's input must be preserved intact
        user_msg = conv_store.get_message(u2.id)
        assert user_msg is not None
        assert user_msg.body_md == "Second prompt"

        # Audit record created for capacity saturation
        audit_entries = conv_store._audit_store.list_entries(action="chat_capacity")
        assert len(audit_entries) == 1
        assert audit_entries[0].outcome == "busy"
        assert audit_entries[0].detail.get("failure_class") == "chat_capacity"

    # Clean up first turn
    pool.release(role="barb")


@pytest.mark.asyncio
async def test_slot_freed_during_wait_is_acquired(conv_store: ConversationStore) -> None:
    """When all slots are initially busy but one is released during timeout wait, turn succeeds."""
    pool = ChatConcurrencyPool(max_concurrency=1, barb_reserved=1)
    assert pool.try_acquire(role="barb") is True

    thread = conv_store.create_thread(title="Wait Thread", role="barb", created_by="bob")
    u = conv_store.add_message(thread.id, author_kind="user", author="bob", body_md="Wait prompt")
    p = conv_store.add_message(
        thread.id,
        author_kind="role",
        author="barb",
        kind="text",
        body_md="",
        delivery="pending",
        meta={"in_reply_to": u.id},
    )

    runner = ChatTurnRunner(conv_store=conv_store, pool=pool, acquire_timeout=0.3)

    # Schedule release after 0.05 seconds
    async def delayed_release() -> None:
        await asyncio.sleep(0.05)
        pool.release(role="barb")

    mock_lines = [
        json.dumps({"type": "content_block_delta", "delta": {"text": "Acquired slot after waiting."}}),
    ]

    with patch.object(runner, "_spawn_cli_process") as mock_spawn:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = iter(mock_lines)
        mock_proc.stderr = iter([])
        mock_proc.poll.return_value = 0
        mock_spawn.return_value = mock_proc

        asyncio.create_task(delayed_release())
        res = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=u.id,
            placeholder_id=p.id,
            role_name="barb",
            provider="claude",
        )

        assert res.ok is True
        assert "Acquired slot after waiting." in res.reply
        assert pool.active == 0


@pytest.mark.asyncio
async def test_pool_size_4_non_barb_saturation_and_barb_access(conv_store: ConversationStore) -> None:
    """Pool size 4 with barb_reserved=1: 4th non-barb turn is busy, but Barb turn succeeds."""
    pool = ChatConcurrencyPool(max_concurrency=4, barb_reserved=1)

    # 3 non-barb slots acquired
    assert pool.try_acquire("night-watch") is True
    assert pool.try_acquire("cartographer") is True
    assert pool.try_acquire("librarian") is True

    thread = conv_store.create_thread(title="Pool4 Thread", role="sanitation", created_by="carol")
    u_nb = conv_store.add_message(thread.id, author_kind="user", author="carol", body_md="Sanitation prompt")
    p_nb = conv_store.add_message(thread.id, author_kind="role", author="sanitation", delivery="pending")

    runner = ChatTurnRunner(conv_store=conv_store, pool=pool, acquire_timeout=0.01)

    with patch.object(runner, "_spawn_cli_process") as mock_spawn:
        # Non-barb 4th turn must get chat_capacity (reserved for Barb)
        res_nb = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=u_nb.id,
            placeholder_id=p_nb.id,
            role_name="sanitation",
            provider="claude",
        )
        assert res_nb.ok is False
        assert res_nb.failure_class == "chat_capacity"
        mock_spawn.assert_not_called()

        # But Barb CAN acquire the 4th slot!
        u_b = conv_store.add_message(thread.id, author_kind="user", author="carol", body_md="Barb prompt")
        p_b = conv_store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = iter([json.dumps({"type": "content_block_delta", "delta": {"text": "Barb reply"}})])
        mock_proc.stderr = iter([])
        mock_proc.poll.return_value = 0
        mock_spawn.return_value = mock_proc

        res_b = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=u_b.id,
            placeholder_id=p_b.id,
            role_name="barb",
            provider="claude",
        )
        assert res_b.ok is True
        assert "Barb reply" in res_b.reply

    # Clean up
    pool.release("night-watch")
    pool.release("cartographer")
    pool.release("librarian")


@pytest.mark.asyncio
async def test_bus_publishes_capacity_events(conv_store: ConversationStore) -> None:
    """Capacity failure publishes failed reply message and system busy notice to event bus."""
    pool = ChatConcurrencyPool(max_concurrency=1, barb_reserved=1)
    assert pool.try_acquire(role="barb") is True

    thread = conv_store.create_thread(title="Bus Test", role="barb", created_by="dave")
    u = conv_store.add_message(thread.id, author_kind="user", author="dave", body_md="Check status")
    p = conv_store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")

    bus = get_thread_bus()
    queue = await bus.subscribe(thread.id)

    runner = ChatTurnRunner(conv_store=conv_store, pool=pool, acquire_timeout=0.01)
    res = await runner.execute_turn(
        thread_id=thread.id,
        user_message_id=u.id,
        placeholder_id=p.id,
        role_name="barb",
        provider="claude",
    )
    assert res.ok is False
    assert res.failure_class == "chat_capacity"

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    msg_events = [e for e in events if e.get("event") == "message"]
    assert len(msg_events) >= 2
    failed_payloads = [
        e["data"]["message"] for e in msg_events if e.get("data", {}).get("message", {}).get("delivery") == "failed"
    ]
    assert len(failed_payloads) == 1
    assert failed_payloads[0]["id"] == p.id

    system_payloads = [
        e["data"]["message"] for e in msg_events if e.get("data", {}).get("message", {}).get("author_kind") == "system"
    ]
    assert len(system_payloads) == 1
    assert "busy" in system_payloads[0]["body_md"].lower()

    pool.release(role="barb")


@pytest.mark.unit
def test_reset_chat_pool_and_env_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """STAFF_MAX_CHAT_TURNS configures singleton on get_chat_pool(); reset_chat_pool clears it."""
    reset_chat_pool()
    monkeypatch.setenv("STAFF_MAX_CHAT_TURNS", "2")
    p1 = get_chat_pool()
    assert p1.max_concurrency == 2
    assert get_chat_pool() is p1

    reset_chat_pool()
    monkeypatch.setenv("STAFF_MAX_CHAT_TURNS", "5")
    p2 = get_chat_pool()
    assert p2.max_concurrency == 5
    assert p2 is not p1
    reset_chat_pool()


@pytest.mark.asyncio
async def test_handle_capacity_exhausted_preconditions(conv_store: ConversationStore) -> None:
    """handle_capacity_exhausted validates non-empty preconditions."""
    with pytest.raises(ValueError, match="thread_id must be non-empty"):
        await handle_capacity_exhausted(conv_store, "", "u1", "p1", "barb")

    with pytest.raises(ValueError, match="placeholder_id must be non-empty"):
        await handle_capacity_exhausted(conv_store, "t1", "u1", "", "barb")

    with pytest.raises(ValueError, match="role_name must be non-empty"):
        await handle_capacity_exhausted(conv_store, "t1", "u1", "p1", "   ")
