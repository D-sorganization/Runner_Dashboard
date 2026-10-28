"""A role reply ending ``handoff: <role>`` becomes a handoff card and moves the work (#1548)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from staff import chat
from staff.chat import ChatTurnResult, run_chat_turn_in_background
from staff.chat_handoff import handoff_reason, post_reply_handoff
from staff.conversations import ConversationStore, get_conversation_store, reset_conversation_store

KNOWN = frozenset({"barb", "e2e-analyst", "librarian"})


@pytest.fixture(autouse=True)
def clean_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    reset_conversation_store()
    yield
    reset_conversation_store()


class _Bus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish_message(self, thread_id: str, message_dict: dict[str, Any]) -> int:
        self.published.append((thread_id, message_dict))
        return 1


def _ask(store: ConversationStore, text: str = "analyse the queue") -> tuple[str, str]:
    th = store.create_thread(title="Ask Barb", kind="auto", participants=["barb", "dieter"])
    msg = store.add_message(thread_id=th.id, author_kind="user", author="dieter", body_md=text)
    return th.id, msg.id


def _handoff(store: ConversationStore, bus: _Bus, **overrides: Any) -> Any:
    thread_id, user_message_id = _ask(store)
    kwargs: dict[str, Any] = {
        "thread_id": thread_id,
        "user_message_id": user_message_id,
        "from_role": "barb",
        "to_role": "e2e-analyst",
        "reply": "That is an analysis question, so I am handing it over.",
        "caller_id": "dieter",
        "known_roles": KNOWN,
        "store": store,
        "bus": bus,
    }
    kwargs.update(overrides)
    return thread_id, asyncio.run(post_reply_handoff(**kwargs))


def test_a_reply_handoff_posts_a_handoff_card_in_the_source_thread() -> None:
    store, bus = get_conversation_store(), _Bus()

    thread_id, result = _handoff(store, bus)

    cards = [m for m in store.list_messages(thread_id) if m.kind == "handoff"]
    assert len(cards) == 1
    meta = cards[0].meta
    assert (meta["from_role"], meta["to_role"]) == ("barb", "e2e-analyst")
    assert meta["reason"] == "That is an analysis question, so I am handing it over."
    assert meta["target_thread_id"] == result.target_thread_id
    assert [t for t, _ in bus.published] == [thread_id]
    assert bus.published[0][1]["kind"] == "handoff"


def test_the_target_role_gets_a_direct_thread_seeded_with_the_request() -> None:
    store, bus = get_conversation_store(), _Bus()

    _, result = _handoff(store, bus)

    target = store.get_thread(result.target_thread_id)
    assert target is not None and target.kind == "direct"
    assert set(target.participants) == {"e2e-analyst", "dieter"}
    brief = store.list_messages(result.target_thread_id)[0]
    assert brief.author == "barb"
    assert "Hand-off from Barb" in brief.body_md and "analyse the queue" in brief.body_md


def test_a_second_handoff_continues_the_existing_target_thread() -> None:
    store, bus = get_conversation_store(), _Bus()

    _, first = _handoff(store, bus)
    _, second = _handoff(store, bus)

    assert second.target_thread_id == first.target_thread_id


def test_a_specialist_hands_off_under_its_own_name() -> None:
    store, bus = get_conversation_store(), _Bus()

    thread_id, result = _handoff(store, bus, from_role="librarian", to_role="e2e-analyst")

    card = next(m for m in store.list_messages(thread_id) if m.kind == "handoff")
    assert card.author == "librarian" and card.meta["from_role"] == "librarian"
    assert card.body_md.startswith("Librarian → E2E Analyst")
    assert store.list_messages(result.target_thread_id)[0].author == "librarian"


@pytest.mark.parametrize("to_role", ["ghost", "barb"])
def test_an_unknown_or_self_handoff_is_ignored(to_role: str) -> None:
    store, bus = get_conversation_store(), _Bus()

    thread_id, result = _handoff(store, bus, to_role=to_role)

    assert result is None
    assert not [m for m in store.list_messages(thread_id) if m.kind == "handoff"]
    assert bus.published == []


def test_the_reason_is_the_first_paragraph_of_the_reply_capped() -> None:
    assert handoff_reason("First line.\n\nSecond paragraph.") == "First line."
    assert handoff_reason("") == "Handed over without a reason."
    assert len(handoff_reason("x" * 1000)) <= 280


def test_the_background_turn_posts_the_handoff_the_reply_named() -> None:
    result = ChatTurnResult(ok=True, reply="Over to the analyst.", handoff="e2e-analyst")
    with (
        patch.object(chat.ChatTurnRunner, "execute_turn", AsyncMock(return_value=result)),
        patch.object(chat, "post_reply_handoff", AsyncMock()) as post,
    ):
        asyncio.run(run_chat_turn_in_background("th-1", "m-1", "p-1", "barb", "dieter"))

    post.assert_awaited_once()
    assert post.await_args.kwargs == {
        "thread_id": "th-1",
        "user_message_id": "m-1",
        "from_role": "barb",
        "to_role": "e2e-analyst",
        "reply": "Over to the analyst.",
        "caller_id": "dieter",
    }


@pytest.mark.parametrize(
    "result",
    [ChatTurnResult(ok=True, reply="Done."), ChatTurnResult(ok=False, handoff="e2e-analyst")],
)
def test_the_background_turn_without_a_successful_handoff_posts_nothing(result: ChatTurnResult) -> None:
    with (
        patch.object(chat.ChatTurnRunner, "execute_turn", AsyncMock(return_value=result)),
        patch.object(chat, "post_reply_handoff", AsyncMock()) as post,
    ):
        asyncio.run(run_chat_turn_in_background("th-1", "m-1", "p-1", "barb", "dieter"))

    post.assert_not_awaited()
