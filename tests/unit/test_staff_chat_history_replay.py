"""Unit tests for chat history replay prompt formatting (Issue #1631).

Verifies:
a) system ack message text does not appear in the replay output;
b) the current question appears exactly once (count == 1) when it is the last stored user message;
c) an earlier identical question (followed by a role reply) is kept, so the text appears twice in total;
d) a normal prior user+role exchange still appears under "### Prior Conversation".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from staff.chat_history import format_history_replay
from staff.conversations import (
    ConversationStore,
    reset_conversation_store,
)
from staff.roles import RoleSpec


@pytest.fixture
def conv_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ConversationStore]:
    """Provide an isolated ConversationStore backed by a temporary SQLite database."""
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    store = ConversationStore(path=db_file)
    yield store
    store.close()
    reset_conversation_store()


@pytest.fixture
def barb_role() -> RoleSpec:
    """Sample RoleSpec for persona injection testing."""
    return RoleSpec(
        name="barb",
        title="Barb",
        summary="Personal secretary.",
        instructions="Handle secretary tasks and answer operator questions.",
    )


@pytest.mark.unit
def test_system_ack_message_not_in_replay_output(
    conv_store: ConversationStore,
    barb_role: RoleSpec,
) -> None:
    """System ack/notice messages must be skipped in history replay (test a, #1631)."""
    thread = conv_store.create_thread(title="System Ack Test", role="barb")
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="alice",
        kind="text",
        body_md="Please route this task to Barb.",
    )
    system_ack = "On it: routing to barb..."
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="system",
        author="system",
        kind="text",
        body_md=system_ack,
    )
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author="barb",
        kind="text",
        body_md="I have received the task and started processing.",
    )

    replay = format_history_replay(
        conv_store=conv_store,
        thread_id=thread.id,
        current_prompt="What is the current status?",
        role=barb_role,
    )

    assert system_ack not in replay
    assert "User: Please route this task to Barb." in replay
    assert "Barb: I have received the task and started processing." in replay
    assert "User: What is the current status?" in replay


@pytest.mark.unit
def test_current_question_appears_once_when_last_stored(
    conv_store: ConversationStore,
    barb_role: RoleSpec,
) -> None:
    """Current question appears exactly once when it is the last stored user message (test b, #1631)."""
    thread = conv_store.create_thread(title="Last User Message Test", role="barb")
    current_prompt = "What is the runner status?"

    # Prior exchange
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="alice",
        kind="text",
        body_md="Good morning Barb.",
    )
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author="barb",
        kind="text",
        body_md="Good morning Alice!",
    )
    # The current user question is already persisted in the store
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="alice",
        kind="text",
        body_md=current_prompt,
    )

    replay = format_history_replay(
        conv_store=conv_store,
        thread_id=thread.id,
        current_prompt=current_prompt,
        role=barb_role,
    )

    assert replay.count(current_prompt) == 1
    assert "### Prior Conversation" in replay
    assert "User: Good morning Barb." in replay
    assert "Barb: Good morning Alice!" in replay
    assert f"User: {current_prompt}\nAssistant:" in replay


@pytest.mark.unit
def test_current_question_appears_once_with_system_ack_after_it(
    conv_store: ConversationStore,
    barb_role: RoleSpec,
) -> None:
    """Current question is dropped from prior turns even if followed by a system notice (#1631)."""
    thread = conv_store.create_thread(title="System Ack Trailing Test", role="barb")
    current_prompt = "Restart runner-3 please."

    conv_store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="alice",
        kind="text",
        body_md="Hello Barb.",
    )
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author="barb",
        kind="text",
        body_md="Hello Alice, ready for instructions.",
    )
    # User message stored
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="alice",
        kind="text",
        body_md=current_prompt,
    )
    # System routing notice stored after user message
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="system",
        author="system",
        kind="text",
        body_md="On it: routing to barb...",
    )

    replay = format_history_replay(
        conv_store=conv_store,
        thread_id=thread.id,
        current_prompt=current_prompt,
        role=barb_role,
    )

    assert "On it: routing to barb..." not in replay
    assert replay.count(current_prompt) == 1
    assert "### Prior Conversation" in replay
    assert "User: Hello Barb." in replay
    assert "Barb: Hello Alice, ready for instructions." in replay
    assert f"User: {current_prompt}\nAssistant:" in replay


@pytest.mark.unit
def test_earlier_identical_question_kept_when_followed_by_role_reply(
    conv_store: ConversationStore,
    barb_role: RoleSpec,
) -> None:
    """An earlier identical question followed by a reply is kept; text appears twice (test c, #1631)."""
    thread = conv_store.create_thread(title="Repeated Question Test", role="barb")
    question = "What is the status?"

    # Turn 1: user asks question
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="alice",
        kind="text",
        body_md=question,
    )
    # Role replies
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author="barb",
        kind="text",
        body_md="All runners are operational.",
    )
    # Turn 2: user asks identical question again (stored in DB before replay)
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="alice",
        kind="text",
        body_md=question,
    )

    replay = format_history_replay(
        conv_store=conv_store,
        thread_id=thread.id,
        current_prompt=question,
        role=barb_role,
    )

    assert replay.count(question) == 2
    assert "### Prior Conversation" in replay
    assert f"User: {question}" in replay
    assert "Barb: All runners are operational." in replay
    assert f"User: {question}\nAssistant:" in replay


@pytest.mark.unit
def test_normal_prior_exchange_appears_under_prior_conversation(
    conv_store: ConversationStore,
    barb_role: RoleSpec,
) -> None:
    """Normal prior user and role exchanges appear under '### Prior Conversation' (test d, #1631)."""
    thread = conv_store.create_thread(title="Normal Prior Exchange Test", role="barb")

    conv_store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="alice",
        kind="text",
        body_md="Can you check the CI logs for run 42?",
    )
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author="barb",
        kind="text",
        body_md="Run 42 passed all test suites without errors.",
    )
    current_prompt = "Great, deploy to staging please."
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="alice",
        kind="text",
        body_md=current_prompt,
    )

    replay = format_history_replay(
        conv_store=conv_store,
        thread_id=thread.id,
        current_prompt=current_prompt,
        role=barb_role,
    )

    assert "### Prior Conversation" in replay
    assert "User: Can you check the CI logs for run 42?" in replay
    assert "Barb: Run 42 passed all test suites without errors." in replay
    assert f"User: {current_prompt}\nAssistant:" in replay
    assert replay.count(current_prompt) == 1


@pytest.mark.unit
def test_single_user_message_produces_no_prior_conversation_section(
    conv_store: ConversationStore,
    barb_role: RoleSpec,
) -> None:
    """When the current user message is the only message in thread, Prior Conversation is omitted."""
    thread = conv_store.create_thread(title="Single Message Test", role="barb")
    current_prompt = "Initial greeting"
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="alice",
        kind="text",
        body_md=current_prompt,
    )

    replay = format_history_replay(
        conv_store=conv_store,
        thread_id=thread.id,
        current_prompt=current_prompt,
        role=barb_role,
    )

    assert replay.count(current_prompt) == 1
    assert "### Prior Conversation" not in replay
    assert f"User: {current_prompt}\nAssistant:" in replay


@pytest.mark.unit
def test_current_question_matching_with_whitespace_stripping(
    conv_store: ConversationStore,
    barb_role: RoleSpec,
) -> None:
    """Whitespace stripping ensures the last user message matches current_prompt (#1631)."""
    thread = conv_store.create_thread(title="Whitespace Strip Test", role="barb")
    conv_store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="alice",
        kind="text",
        body_md="  What is the queue depth?  \n",
    )

    replay = format_history_replay(
        conv_store=conv_store,
        thread_id=thread.id,
        current_prompt="What is the queue depth?",
        role=barb_role,
    )

    assert replay.count("What is the queue depth?") == 1
    assert "### Prior Conversation" not in replay
