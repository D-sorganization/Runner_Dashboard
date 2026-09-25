"""Unit tests for staff conversational chat turns (SC-B4, Issue #1307).

Tests:
1. Adapter chat_argv recipes: read-only/plan mode (no bypass permissions), session resume.
2. Provider session ID extraction across stream events.
3. Dedicated chat concurrency pool with Barb reservation (SC-C6).
4. ChatTurnRunner execution: streaming tokens, metrics (time to first token, turn duration).
5. Two-turn conversation context retention and session resume.
6. Session resume failure fallback to history replay.
7. Read-only scratch execution preventing repository modification.
8. Action proposal generation and persistence from reply contract.
9. Failure classification and error message generation on provider failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from staff.adapters import get_adapter
from staff.chat import (
    ChatConcurrencyPool,
    ChatTurnRunner,
    extract_session_id,
    format_history_replay,
)
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
    reset_conversation_store,
)
from staff.roles import RoleSpec
from staff.thread_bus import get_thread_bus, reset_thread_bus


@pytest.fixture
def conv_store(tmp_path: Path) -> ConversationStore:
    reset_conversation_store()
    reset_thread_bus()
    db_file = tmp_path / "test_chat_conv.sqlite3"
    return get_conversation_store(db_file)


# ── 1. ADAPTER CHAT_ARGV RECIPES ─────────────────────────────────────────────


@pytest.mark.unit
def test_claude_chat_argv_read_only_default_mode() -> None:
    adapter = get_adapter("claude")
    argv = adapter.chat_argv("Explain the architecture", "/tmp/scratch", model="sonnet")
    assert argv[0] == "claude"
    assert "-p" in argv
    assert "Explain the architecture" in argv
    assert "stream-json" in argv
    assert "--verbose" in argv
    # Bypass permissions must NOT be present in conversational turns
    assert "bypassPermissions" not in argv
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] in {"default", "plan"}
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert "--resume" not in argv


@pytest.mark.unit
def test_claude_chat_argv_session_resume() -> None:
    adapter = get_adapter("claude")
    argv = adapter.chat_argv("What did I say earlier?", "/tmp/scratch", session_id="claude_sess_123")
    assert "--resume" in argv
    assert argv[argv.index("--resume") + 1] == "claude_sess_123"
    assert "bypassPermissions" not in argv


@pytest.mark.unit
def test_codex_chat_argv_read_only_mode() -> None:
    adapter = get_adapter("codex")
    argv = adapter.chat_argv("Review PR 100", "/tmp/scratch", model="o3-mini")
    assert argv[0] == "codex"
    assert "exec" in argv
    # Must NOT have dangerous bypass flags
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert "--sandbox" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "Review PR 100" in argv
    assert "--resume" not in argv


@pytest.mark.unit
def test_codex_chat_argv_session_resume() -> None:
    adapter = get_adapter("codex")
    argv = adapter.chat_argv("Follow up question", "/tmp/scratch", session_id="codex_session_abc")
    assert "resume" in argv or "--session" in argv
    assert "codex_session_abc" in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv


@pytest.mark.unit
def test_antigravity_cursor_gemini_chat_argv() -> None:
    # Antigravity
    agy = get_adapter("antigravity")
    agy_argv = agy.chat_argv("Hello Antigravity", "/tmp/scratch", session_id="agy_conv_456")
    assert agy_argv[0] == "agy"
    assert "--dangerously-skip-permissions" not in agy_argv
    assert "agy_conv_456" in agy_argv

    # Cursor-agent
    cursor = get_adapter("cursor-agent")
    c_argv = cursor.chat_argv("Hello Cursor", "/tmp/scratch", session_id="cursor_chat_789")
    assert c_argv[0] == "cursor-agent"
    assert "--force" not in c_argv
    assert "--trust" not in c_argv
    assert "cursor_chat_789" in c_argv

    # Gemini
    gemini = get_adapter("gemini")
    g_argv = gemini.chat_argv("Hello Gemini", "/tmp/scratch")
    assert g_argv[0] == "gemini"
    assert "-p" in g_argv


# ── 2. SESSION ID EXTRACTION ─────────────────────────────────────────────────


@pytest.mark.unit
def test_extract_session_id_from_stream_events() -> None:
    # Claude init event
    claude_event = {"kind": "init", "raw": {"type": "init", "session_id": "claude_sess_abc123"}}
    assert extract_session_id("claude", claude_event) == "claude_sess_abc123"

    # Cursor init event
    cursor_event = {"kind": "init", "raw": {"type": "init", "chat_id": "cursor_chat_xyz"}}
    assert extract_session_id("cursor-agent", cursor_event) == "cursor_chat_xyz"

    # Antigravity result / init event
    agy_event = {"kind": "init", "raw": {"event": "init", "conversation_id": "agy_conv_999"}}
    assert extract_session_id("antigravity", agy_event) == "agy_conv_999"

    # Codex text output with Session:
    codex_event = {"kind": "text", "text": "Session: codex_sess_555\nReady."}
    assert extract_session_id("codex", codex_event) == "codex_sess_555"

    # None when not found
    plain_event = {"kind": "text", "text": "Just regular message"}
    assert extract_session_id("claude", plain_event) is None


# ── 3. CHAT CONCURRENCY POOL & BARB RESERVATION (SC-C6) ──────────────────────


@pytest.mark.unit
def test_chat_concurrency_pool_limits_and_barb_reservation() -> None:
    pool = ChatConcurrencyPool(max_concurrency=4, barb_reserved=1)

    # Acquire 3 non-Barb slots
    assert pool.try_acquire(role="night-watch") is True
    assert pool.try_acquire(role="cartographer") is True
    assert pool.try_acquire(role="librarian") is True

    # 4th slot is reserved for Barb: another non-Barb role CANNOT acquire it
    assert pool.try_acquire(role="sanitation") is False

    # But Barb CAN acquire the reserved slot!
    assert pool.try_acquire(role="barb") is True

    # Now all 4 slots are occupied; even Barb cannot exceed pool limit
    assert pool.try_acquire(role="barb") is False

    # Release one non-Barb slot
    pool.release(role="night-watch")

    # Now non-Barb still cannot acquire because current active = 3 >= (4 - 1)
    assert pool.try_acquire(role="sanitation") is False

    # Release Barb slot (active becomes 2 < 3)
    pool.release(role="barb")

    # Now non-Barb can acquire
    assert pool.try_acquire(role="sanitation") is True


# ── 4. HISTORY REPLAY FORMATTING ─────────────────────────────────────────────


@pytest.mark.unit
def test_format_history_replay_with_persona_and_budget(conv_store: ConversationStore) -> None:
    t = conv_store.create_thread(title="Test Replay", role="barb", created_by="user1")
    conv_store.add_message(t.id, author_kind="user", author="user1", body_md="First user question")
    conv_store.add_message(t.id, author_kind="role", author="barb", body_md="First role answer")

    role = RoleSpec(
        name="barb",
        title="Barb",
        summary="Front door secretary",
        instructions="You are Barb, secretary and front door.",
    )

    prompt = format_history_replay(
        conv_store=conv_store,
        thread_id=t.id,
        current_prompt="Second user question",
        role=role,
        token_budget=2000,
    )

    assert "You are Barb, secretary and front door." in prompt
    assert "First user question" in prompt
    assert "First role answer" in prompt
    assert "Second user question" in prompt


# ── 5. EXECUTE CHAT TURN (STREAMING, METRICS, REPLY CONTRACT) ────────────────


@pytest.mark.asyncio
async def test_execute_chat_turn_success_and_metrics(conv_store: ConversationStore) -> None:
    thread = conv_store.create_thread(title="Chat Test", role="barb", created_by="alice")
    user_msg = conv_store.add_message(thread.id, author_kind="user", author="alice", body_md="What is the status?")
    placeholder = conv_store.add_message(
        thread.id,
        author_kind="role",
        author="barb",
        kind="text",
        body_md="",
        delivery="pending",
        meta={"in_reply_to": user_msg.id},
    )

    bus = get_thread_bus()
    queue = await bus.subscribe(thread.id)

    # Simulated stream lines from provider CLI
    mock_lines = [
        json.dumps({"type": "init", "session_id": "claude_sess_live_123"}),
        json.dumps({"type": "content_block_delta", "delta": {"text": "All runners are "}}),
        json.dumps({"type": "content_block_delta", "delta": {"text": "healthy.\n\nhandoff: night-watch"}}),
    ]

    runner = ChatTurnRunner(conv_store=conv_store)

    with patch.object(runner, "_spawn_cli_process") as mock_spawn:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = iter(mock_lines)
        mock_proc.stderr = iter([])
        mock_proc.poll.return_value = 0
        mock_spawn.return_value = mock_proc

        result = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=user_msg.id,
            placeholder_id=placeholder.id,
            role_name="barb",
            provider="claude",
        )

        assert result.ok is True
        assert "All runners are healthy." in result.reply
        assert result.session_id == "claude_sess_live_123"
        assert result.handoff == "night-watch"

        # Check message updated in store
        msg = conv_store.get_message(placeholder.id)
        assert msg is not None
        assert msg.delivery == "complete"
        assert "All runners are healthy." in msg.body_md
        assert msg.meta.get("handoff") == "night-watch"
        assert msg.meta.get("session_id") == "claude_sess_live_123"
        assert "metrics" in msg.meta
        metrics = msg.meta["metrics"]
        assert "time_to_first_token_seconds" in metrics
        assert "turn_duration_seconds" in metrics
        assert metrics["provider"] == "claude"

        # Check thread has stored session_id
        t_after = conv_store.get_thread(thread.id)
        assert t_after is not None
        assert t_after.meta.get("provider_sessions", {}).get("claude") == "claude_sess_live_123"

        # Check bus received token events
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        token_events = [e for e in events if e.get("event") == "token"]
        assert len(token_events) >= 1


# ── 6. TWO-TURN CONVERSATION CONTEXT AND SESSION RESUME ──────────────────────


@pytest.mark.asyncio
async def test_two_turn_conversation_resumes_session(conv_store: ConversationStore) -> None:
    thread = conv_store.create_thread(title="Two Turn", role="barb", created_by="bob")

    # Turn 1
    u1 = conv_store.add_message(thread.id, author_kind="user", author="bob", body_md="My name is Bob")
    p1 = conv_store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")

    runner = ChatTurnRunner(conv_store=conv_store)

    turn1_lines = [
        json.dumps({"type": "init", "session_id": "sess_turn_1"}),
        json.dumps({"type": "content_block_delta", "delta": {"text": "Nice to meet you, Bob."}}),
    ]

    with patch.object(runner, "_spawn_cli_process") as mock_spawn:
        mock_proc1 = MagicMock()
        mock_proc1.returncode = 0
        mock_proc1.stdout = iter(turn1_lines)
        mock_proc1.stderr = iter([])
        mock_proc1.poll.return_value = 0
        mock_spawn.return_value = mock_proc1

        res1 = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=u1.id,
            placeholder_id=p1.id,
            role_name="barb",
            provider="claude",
        )
        assert res1.session_id == "sess_turn_1"

    # Turn 2: verify session_id "sess_turn_1" is passed to chat_argv
    u2 = conv_store.add_message(thread.id, author_kind="user", author="bob", body_md="What is my name?")
    p2 = conv_store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")

    captured_session_id = None

    turn2_lines = [
        json.dumps({"type": "init", "session_id": "sess_turn_1"}),
        json.dumps({"type": "content_block_delta", "delta": {"text": "Your name is Bob."}}),
    ]

    def capture_spawn(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal captured_session_id
        cmd = kwargs.get("cmd") or args[0]
        if "--resume" in cmd:
            captured_session_id = cmd[cmd.index("--resume") + 1]
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = iter(turn2_lines)
        proc.stderr = iter([])
        proc.poll.return_value = 0
        return proc

    with patch.object(runner, "_spawn_cli_process", side_effect=capture_spawn):
        res2 = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=u2.id,
            placeholder_id=p2.id,
            role_name="barb",
            provider="claude",
        )
        assert res2.ok is True
        assert captured_session_id == "sess_turn_1"


# ── 7. RESUME FAILURE FALLS BACK TO REPLAY ───────────────────────────────────


@pytest.mark.asyncio
async def test_resume_failure_fallback_to_replay(conv_store: ConversationStore) -> None:
    thread = conv_store.create_thread(title="Fallback Replay", role="barb", created_by="carol")
    # Store expired session
    conv_store.update_thread(thread.id, meta={"provider_sessions": {"claude": "expired_sess_999"}})

    u = conv_store.add_message(thread.id, author_kind="user", author="carol", body_md="Check status")
    p = conv_store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")

    runner = ChatTurnRunner(conv_store=conv_store)

    calls = 0

    def mock_spawn(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal calls
        calls += 1
        cmd = kwargs.get("cmd") or args[0]
        proc = MagicMock()
        if "--resume" in cmd:
            # First call fails: session expired
            proc.returncode = 1
            proc.stdout = iter([])
            proc.stderr = iter(["Error: session expired_sess_999 not found"])
            proc.poll.return_value = 1
        else:
            # Second call (replay) succeeds
            proc.returncode = 0
            proc.stdout = iter(
                [json.dumps({"type": "content_block_delta", "delta": {"text": "Recovered via replay."}})]
            )
            proc.stderr = iter([])
            proc.poll.return_value = 0
        return proc

    with patch.object(runner, "_spawn_cli_process", side_effect=mock_spawn):
        res = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=u.id,
            placeholder_id=p.id,
            role_name="barb",
            provider="claude",
        )
        assert calls == 2  # First attempted resume, second fell back to replay
        assert res.ok is True
        assert "Recovered via replay." in res.reply
        msg = conv_store.get_message(p.id)
        assert msg is not None
        assert msg.meta.get("replayed_history") is True


# ── 8. ACTION PROPOSALS FROM REPLY CONTRACT (SC-B6) ──────────────────────────


@pytest.mark.asyncio
async def test_chat_turn_action_proposals_created(conv_store: ConversationStore) -> None:
    thread = conv_store.create_thread(title="Action Test", role="maintenance", created_by="admin")
    u = conv_store.add_message(thread.id, author_kind="user", author="admin", body_md="Restart runner 1")
    p = conv_store.add_message(thread.id, author_kind="role", author="maintenance", delivery="pending")

    reply_payload = (
        "I will restart runner-1 for you.\n\n"
        "```staff-actions\n"
        '[{"action": "runner.restart", "params": {"runner": "runner-1"}, "reason": "stalled"}]\n'
        "```\n"
    )

    runner = ChatTurnRunner(conv_store=conv_store)

    with patch.object(runner, "_spawn_cli_process") as mock_spawn:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = iter([json.dumps({"type": "text", "text": reply_payload})])
        mock_proc.stderr = iter([])
        mock_proc.poll.return_value = 0
        mock_spawn.return_value = mock_proc

        res = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=u.id,
            placeholder_id=p.id,
            role_name="maintenance",
            provider="claude",
        )
        assert res.ok is True
        assert len(res.actions) == 1
        assert res.actions[0].action == "runner.restart"

        # Check action proposal created in store
        proposals = conv_store.list_proposals(thread_id=thread.id)
        assert len(proposals) == 1
        prop = proposals[0]
        assert prop.action == "runner.restart"
        assert prop.params == {"runner": "runner-1"}
        assert prop.state == "proposed"


# ── 9. FAILURE CLASSIFICATION AND ERROR RECORDING ────────────────────────────


@pytest.mark.asyncio
async def test_chat_turn_failure_classified_and_recorded(conv_store: ConversationStore) -> None:
    thread = conv_store.create_thread(title="Fail Test", role="barb", created_by="dave")
    u = conv_store.add_message(thread.id, author_kind="user", author="dave", body_md="Check status")
    p = conv_store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")

    runner = ChatTurnRunner(conv_store=conv_store)

    with patch.object(runner, "_spawn_cli_process") as mock_spawn:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = iter([])
        mock_proc.stderr = iter(["401 Unauthorized: token expired"])
        mock_proc.poll.return_value = 1
        mock_spawn.return_value = mock_proc

        res = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=u.id,
            placeholder_id=p.id,
            role_name="barb",
            provider="claude",
        )
        assert res.ok is False
        assert res.failure_class == "auth_expired"

        msg = conv_store.get_message(p.id)
        assert msg is not None
        assert msg.kind == "error"
        assert msg.delivery == "failed"
        assert msg.meta.get("failure_class") == "auth_expired"
        assert any(a.get("name") == "retry" for a in msg.meta.get("actions", []))
