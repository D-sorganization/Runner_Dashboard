"""Unit tests for live token streaming during chat turns (SC-B1-G10, Issue #1493).

Verifies that tokens are published live on the ThreadEventBus incrementally
as provider stdout chunks arrive, BEFORE the provider CLI subprocess exits.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from staff.chat import ChatTurnRunner
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
    db_file = tmp_path / "test_chat_streaming_conv.sqlite3"
    return get_conversation_store(db_file)


@pytest.mark.asyncio
async def test_live_token_streaming_before_process_exit(
    conv_store: ConversationStore,
) -> None:
    """Acceptance criterion: first token event is published before process exits."""
    thread = conv_store.create_thread(
        title="Live Streaming", role="barb", created_by="alice"
    )
    user_msg = conv_store.add_message(
        thread.id, author_kind="user", author="alice", body_md="Stream test"
    )
    placeholder = conv_store.add_message(
        thread.id, author_kind="role", author="barb", delivery="pending"
    )

    bus = get_thread_bus()
    event_queue = await bus.subscribe(thread.id)

    chunk1_sent = threading.Event()
    allow_process_exit = threading.Event()

    def delayed_stdout_generator() -> Any:
        # Initial init event
        yield json.dumps({"type": "init", "session_id": "stream_sess_123"}) + "\n"
        # Chunk 1
        yield json.dumps(
            {"type": "content_block_delta", "delta": {"text": "Live "}}
        ) + "\n"
        chunk1_sent.set()
        # Block process exit until test observes first token live on bus
        if not allow_process_exit.wait(timeout=3.0):
            raise TimeoutError("allow_process_exit was not set in time")
        # Chunk 2
        yield json.dumps(
            {"type": "content_block_delta", "delta": {"text": "streaming reply."}}
        ) + "\n"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = delayed_stdout_generator()
    mock_proc.stderr = iter([])

    def mock_poll() -> int | None:
        return 0 if allow_process_exit.is_set() else None

    def mock_wait() -> int:
        allow_process_exit.wait(timeout=3.0)
        return 0

    mock_proc.poll = mock_poll
    mock_proc.wait = mock_wait

    runner = ChatTurnRunner(conv_store=conv_store)

    with patch.object(runner, "_spawn_cli_process", return_value=mock_proc):
        turn_task = asyncio.create_task(
            runner.execute_turn(
                thread_id=thread.id,
                user_message_id=user_msg.id,
                placeholder_id=placeholder.id,
                role_name="barb",
                provider="claude",
            )
        )

        # Await the first token event on the bus with a timeout
        first_token: str | None = None
        while True:
            event = await asyncio.wait_for(event_queue.get(), timeout=2.0)
            if event.get("event") == "token":
                first_token = event.get("data", {}).get("delta")
                break

        assert first_token == "Live "

        # CRITICAL ASSERTION: The process must still be running (poll is None, task not done)
        assert (
            mock_proc.poll() is None
        ), "Process must not have exited when first token is published"
        assert not turn_task.done(), "Turn task must still be in-flight while streaming"

        # Now unblock the process to finish emitting chunk 2 and exit
        allow_process_exit.set()

        # Await turn completion
        result = await asyncio.wait_for(turn_task, timeout=5.0)

        assert result.ok is True
        assert result.reply == "Live streaming reply."
        assert result.session_id == "stream_sess_123"

        # Final message in store must match
        msg = conv_store.get_message(placeholder.id)
        assert msg is not None
        assert msg.delivery == "complete"
        assert msg.body_md == "Live streaming reply."
        assert msg.meta.get("session_id") == "stream_sess_123"


@pytest.mark.asyncio
async def test_live_streaming_non_zero_exit_code_records_failure(
    conv_store: ConversationStore,
) -> None:
    """Verifies that non-zero exit code classifies error and records failure properly."""
    thread = conv_store.create_thread(
        title="Exit Failure", role="barb", created_by="alice"
    )
    user_msg = conv_store.add_message(
        thread.id, author_kind="user", author="alice", body_md="Fail test"
    )
    placeholder = conv_store.add_message(
        thread.id, author_kind="role", author="barb", delivery="pending"
    )

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = iter(
        [json.dumps({"type": "content_block_delta", "delta": {"text": "Partial "}})]
    )
    mock_proc.stderr = iter(["Fatal error: out of memory"])
    mock_proc.poll.return_value = 1
    mock_proc.wait.return_value = 1

    runner = ChatTurnRunner(conv_store=conv_store)

    with patch.object(runner, "_spawn_cli_process", return_value=mock_proc):
        result = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=user_msg.id,
            placeholder_id=placeholder.id,
            role_name="barb",
            provider="claude",
        )

        assert result.ok is False
        assert result.failure_class is not None

        # Check placeholder in store recorded failure
        msg = conv_store.get_message(placeholder.id)
        assert msg is not None
        assert msg.delivery == "failed"


@pytest.mark.asyncio
async def test_live_streaming_cancellation_kills_process(
    conv_store: ConversationStore,
) -> None:
    """Verifies that cancelling the in-flight chat turn kills the live subprocess."""
    thread = conv_store.create_thread(
        title="Cancel Test", role="barb", created_by="alice"
    )
    user_msg = conv_store.add_message(
        thread.id, author_kind="user", author="alice", body_md="Cancel test"
    )
    placeholder = conv_store.add_message(
        thread.id, author_kind="role", author="barb", delivery="pending"
    )

    bus = get_thread_bus()
    event_queue = await bus.subscribe(thread.id)

    stream_started = threading.Event()
    hang_event = threading.Event()

    def hanging_stdout() -> Any:
        yield json.dumps(
            {"type": "content_block_delta", "delta": {"text": "Start"}}
        ) + "\n"
        stream_started.set()
        hang_event.wait(timeout=5.0)
        yield json.dumps(
            {"type": "content_block_delta", "delta": {"text": "Never reached"}}
        ) + "\n"

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.stdout = hanging_stdout()
    mock_proc.stderr = iter([])
    mock_proc.poll.return_value = None

    runner = ChatTurnRunner(conv_store=conv_store)

    with patch.object(runner, "_spawn_cli_process", return_value=mock_proc):
        task = asyncio.create_task(
            runner.execute_turn(
                thread_id=thread.id,
                user_message_id=user_msg.id,
                placeholder_id=placeholder.id,
                role_name="barb",
                provider="claude",
            )
        )

        # Wait until first token arrives
        while True:
            event = await asyncio.wait_for(event_queue.get(), timeout=2.0)
            if event.get("event") == "token":
                break

        # Cancel the in-flight task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Unblock generator thread cleanup
        hang_event.set()

        # The process should have been killed on cancellation
        mock_proc.kill.assert_called()


@pytest.mark.asyncio
async def test_live_streaming_handles_bytes_stdout(
    conv_store: ConversationStore,
) -> None:
    """Verifies that bytes emitted on stdout are safely decoded without crashing."""
    thread = conv_store.create_thread(
        title="Bytes Test", role="barb", created_by="alice"
    )
    user_msg = conv_store.add_message(
        thread.id, author_kind="user", author="alice", body_md="Bytes test"
    )
    placeholder = conv_store.add_message(
        thread.id, author_kind="role", author="barb", delivery="pending"
    )

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = iter(
        [b'{"type": "content_block_delta", "delta": {"text": "Bytes OK"}}\n']
    )
    mock_proc.stderr = iter([b"some warning\n"])
    mock_proc.poll.return_value = 0
    mock_proc.wait.return_value = 0

    runner = ChatTurnRunner(conv_store=conv_store)

    with patch.object(runner, "_spawn_cli_process", return_value=mock_proc):
        result = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=user_msg.id,
            placeholder_id=placeholder.id,
            role_name="barb",
            provider="claude",
        )

        assert result.ok is True
        assert result.reply == "Bytes OK"
