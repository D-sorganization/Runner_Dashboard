"""A role remembers the previous turn of its thread (#1655).

Two separate failures made Barb forget turn 1 on DeskComputer:

- The claude CLI keeps a session under a project directory keyed by the working
  directory. Every turn ran in a fresh ``mkdtemp`` directory, so ``--resume`` from
  turn 2 never found turn 1's session and the turn always fell back to replay.
- The replay budget was spent on the persona and the gathered fleet context before
  any history, so a role with a long persona and a full fleet block replayed no
  prior turns at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from staff.chat import ChatTurnRunner
from staff.chat_history import format_history_replay
from staff.conversations import ConversationStore, get_conversation_store, reset_conversation_store
from staff.roles import RoleSpec
from staff.thread_bus import reset_thread_bus

CODEWORD = "PERIWINKLE-42"


@pytest.fixture
def conv_store(tmp_path: Path) -> ConversationStore:
    reset_conversation_store()
    reset_thread_bus()
    return get_conversation_store(tmp_path / "test_chat_memory.sqlite3")


def _big_role() -> RoleSpec:
    return RoleSpec(name="barb", title="Barb", summary="Secretary.", instructions="Persona line.\n" * 600)


@pytest.mark.unit
def test_replay_keeps_the_previous_turn_behind_a_long_persona_and_full_context(
    conv_store: ConversationStore,
) -> None:
    thread = conv_store.create_thread(title="memory", role="barb")
    conv_store.add_message(thread.id, author_kind="user", author="dieter", body_md=f"The codeword is {CODEWORD}.")
    conv_store.add_message(thread.id, author_kind="role", author="barb", body_md="Noted.")
    conv_store.add_message(thread.id, author_kind="user", author="dieter", body_md="What is the codeword?")

    replay = format_history_replay(
        conv_store=conv_store,
        thread_id=thread.id,
        current_prompt="What is the codeword?",
        role=_big_role(),
        context="## Fleet now\n" + "x" * 8000,
    )

    assert CODEWORD in replay
    assert replay.index(CODEWORD) < replay.rindex("What is the codeword?")


def _post_turn(conv_store: ConversationStore, thread_id: str, text: str) -> tuple[str, str]:
    user = conv_store.add_message(thread_id, author_kind="user", author="dieter", body_md=text)
    placeholder = conv_store.add_message(
        thread_id,
        author_kind="role",
        author="barb",
        body_md="",
        delivery="pending",
        meta={"in_reply_to": user.id},
    )
    return user.id, placeholder.id


def _proc(session_id: str) -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = iter(
        [
            json.dumps({"type": "init", "session_id": session_id}),
            json.dumps({"type": "content_block_delta", "delta": {"text": "Noted."}}),
        ]
    )
    proc.stderr = iter([])
    proc.poll.return_value = 0
    return proc


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turns_on_one_thread_run_in_the_same_directory_so_resume_finds_the_session(
    conv_store: ConversationStore,
) -> None:
    role = RoleSpec(name="barb", title="Barb", instructions="You are Barb.")
    thread = conv_store.create_thread(title="memory", role="barb")
    other = conv_store.create_thread(title="other", role="barb")
    runner = ChatTurnRunner(conv_store=conv_store)
    cwds: list[str] = []
    argvs: list[list[str]] = []

    def spawn(*, cmd: list[str], cwd: str, env: dict[str, str]) -> MagicMock:
        cwds.append(cwd)
        argvs.append(cmd)
        assert Path(cwd).is_dir()
        return _proc("sess-1")

    with (
        patch("staff.chat.load_roles", return_value={"barb": role}),
        patch("staff.chat.build_fleet_context_block", new_callable=AsyncMock, return_value=None),
        patch.object(runner, "_spawn_cli_process", side_effect=spawn),
    ):
        turns = ((thread.id, f"The codeword is {CODEWORD}."), (thread.id, "Codeword?"), (other.id, "Hi"))
        for thread_id, text in turns:
            user_id, placeholder_id = _post_turn(conv_store, thread_id, text)
            result = await runner.execute_turn(
                thread_id=thread_id,
                user_message_id=user_id,
                placeholder_id=placeholder_id,
                role_name="barb",
                provider="claude",
            )
            assert result.ok is True

    assert cwds[0] == cwds[1], "a thread's turns must share one working directory"
    assert cwds[2] != cwds[0], "threads must not share a working directory"
    assert "--resume" in argvs[1] and argvs[1][argvs[1].index("--resume") + 1] == "sess-1"
