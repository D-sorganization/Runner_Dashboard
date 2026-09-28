"""Unit test for read_issue context wiring into staff chat turns (Runner_Dashboard#1762)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from staff.chat import ChatTurnRunner
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
    reset_conversation_store,
)
from staff.roles import RoleSpec
from staff.thread_bus import reset_thread_bus
from staff.turn_prompt import MESSAGE_HEADER


@pytest.fixture
def conv_store(tmp_path: Path) -> ConversationStore:
    reset_conversation_store()
    reset_thread_bus()
    db_file = tmp_path / "test_chat_issue_prompt.sqlite3"
    return get_conversation_store(db_file)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_turn_passes_issue_context_before_user_text(conv_store: ConversationStore) -> None:
    role = RoleSpec(
        name="barb",
        title="Barb",
        instructions="You are Barb.",
        chat={"tools": ["read_issue"]},
    )

    thread = conv_store.create_thread(
        title="Barb Turn",
        role="barb",
        created_by="alice",
        meta={"provider_sessions": {"claude": "claude_issue_123"}},
    )
    user_msg = conv_store.add_message(
        thread.id,
        author_kind="user",
        author="alice",
        body_md="Take D-sorganization/UpstreamDrift#11080 to the Board.",
    )
    placeholder = conv_store.add_message(
        thread.id,
        author_kind="role",
        author="barb",
        kind="text",
        body_md="",
        delivery="pending",
        meta={"in_reply_to": user_msg.id},
    )

    runner = ChatTurnRunner(conv_store=conv_store)

    mock_lines = [
        json.dumps({"type": "init", "session_id": "claude_issue_123"}),
        json.dumps({"type": "content_block_delta", "delta": {"text": "Taking it to the Board."}}),
    ]

    with (
        patch("staff.chat.load_roles", return_value={"barb": role}),
        patch("staff.chat.build_fleet_context_block", new_callable=AsyncMock, return_value=None),
        patch(
            "staff.chat.build_issue_context_block",
            new_callable=AsyncMock,
            return_value="## Referenced items\nPR #11080: Proposal packet",
        ),
        patch.object(runner, "_spawn_cli_process") as mock_spawn,
    ):
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
        argv = mock_spawn.call_args.kwargs["cmd"]
        prompt = next(arg for arg in argv if "Take D-sorganization/UpstreamDrift#11080" in arg)
        assert "## Referenced items" in prompt
        assert MESSAGE_HEADER in prompt
        issue_idx = prompt.rindex("## Referenced items")
        header_idx = prompt.rindex(MESSAGE_HEADER)
        user_idx = prompt.rindex("Take D-sorganization/UpstreamDrift#11080")
        assert issue_idx < header_idx < user_idx
