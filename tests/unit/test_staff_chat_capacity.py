"""Unit tests for chat pool saturation and Barb reservation (SC-B1-G9, Issue #1492)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from staff.chat import ChatConcurrencyPool, ChatTurnRunner
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
    reset_conversation_store,
)
from staff.thread_bus import reset_thread_bus


@pytest.fixture
def conv_store(tmp_path: Path) -> ConversationStore:
    reset_conversation_store()
    reset_thread_bus()
    db_file = tmp_path / "test_chat_capacity.sqlite3"
    return get_conversation_store(db_file)


@pytest.mark.asyncio
async def test_pool_size_1_second_concurrent_turn_gets_chat_capacity(conv_store: ConversationStore) -> None:
    """When the chat pool is saturated, the turn fails with failure_class='chat_capacity',
    marks the reply failed, and posts a system message.
    """
    pool = ChatConcurrencyPool(max_concurrency=1, barb_reserved=0)
    runner = ChatTurnRunner(conv_store=conv_store, pool=pool)

    # First turn acquires the single slot
    assert pool.try_acquire(role="cartographer") is True

    # Now attempt a concurrent second turn for "librarian"
    thread = conv_store.create_thread(title="Test Capacity", role="librarian", created_by="alice")
    user_msg = conv_store.add_message(thread.id, author_kind="user", author="alice", body_md="Help me find docs")
    placeholder = conv_store.add_message(thread.id, author_kind="role", author="librarian", delivery="pending")

    result = await runner.execute_turn(
        thread_id=thread.id,
        user_message_id=user_msg.id,
        placeholder_id=placeholder.id,
        role_name="librarian",
        provider="claude",
    )

    # Acceptance criteria: second concurrent turn gets chat_capacity
    assert result.ok is False
    assert result.failure_class == "chat_capacity"
    assert result.retryable is True

    # Check placeholder message in conv_store was marked failed
    msg = conv_store.get_message(placeholder.id)
    assert msg is not None
    assert msg.delivery == "failed"
    assert msg.kind == "error"
    assert msg.meta.get("failure_class") == "chat_capacity"
    assert msg.meta.get("retryable") is True

    # Check a system message was posted notifying user all chat slots are busy
    messages = conv_store.list_messages(thread.id)
    system_msgs = [m for m in messages if m.author_kind == "system"]
    assert len(system_msgs) >= 1
    assert any("busy" in m.body_md.lower() and "retry" in m.body_md.lower() for m in system_msgs)


@pytest.mark.asyncio
async def test_barb_still_gets_reserved_slot_when_pool_saturated(conv_store: ConversationStore) -> None:
    """Barb can still acquire its reserved slot even when non-Barb slots are saturated."""
    pool = ChatConcurrencyPool(max_concurrency=2, barb_reserved=1)
    runner = ChatTurnRunner(conv_store=conv_store, pool=pool)

    # Non-Barb slot is acquired
    assert pool.try_acquire(role="cartographer") is True

    # Another non-Barb role cannot acquire and gets chat_capacity
    thread_lib = conv_store.create_thread(title="Librarian Thread", role="librarian", created_by="alice")
    u_lib = conv_store.add_message(thread_lib.id, author_kind="user", author="alice", body_md="Query docs")
    p_lib = conv_store.add_message(thread_lib.id, author_kind="role", author="librarian", delivery="pending")

    res_lib = await runner.execute_turn(
        thread_id=thread_lib.id,
        user_message_id=u_lib.id,
        placeholder_id=p_lib.id,
        role_name="librarian",
        provider="claude",
    )
    assert res_lib.ok is False
    assert res_lib.failure_class == "chat_capacity"

    # But Barb CAN acquire its reserved slot and execute!
    thread_barb = conv_store.create_thread(title="Barb Thread", role="barb", created_by="alice")
    u_barb = conv_store.add_message(thread_barb.id, author_kind="user", author="alice", body_md="Hello Barb")
    p_barb = conv_store.add_message(thread_barb.id, author_kind="role", author="barb", delivery="pending")

    mock_lines = [
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello, I am Barb!"}]}}),
    ]

    with patch.object(runner, "_spawn_cli_process") as mock_spawn:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = iter(mock_lines)
        mock_proc.stderr = iter([])
        mock_proc.poll.return_value = 0
        mock_spawn.return_value = mock_proc

        res_barb = await runner.execute_turn(
            thread_id=thread_barb.id,
            user_message_id=u_barb.id,
            placeholder_id=p_barb.id,
            role_name="barb",
            provider="claude",
        )
        assert res_barb.ok is True
        assert "Hello, I am Barb!" in res_barb.reply

    # After Barb finishes, its slot was released
    assert pool._active == 1  # only cartographer remains active
