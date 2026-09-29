"""Confident auto-route messages go straight to the specialist, with no Barb turn (#1567)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from staff.chat_preroute import (
    PRE_ROUTE_CONFIDENCE_THRESHOLD,
    confident_route,
    preroute_auto_message,
)
from staff.conversations import ConversationStore, get_conversation_store, reset_conversation_store
from staff.router import route_deterministic

KNOWN = frozenset({"barb", "maintenance", "issue-remediator", "librarian"})


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


def _ask(store: ConversationStore, text: str, kind: str = "auto") -> tuple[Any, Any]:
    th = store.create_thread(title="Ask Barb", kind=kind, participants=["barb", "dieter"])
    msg = store.add_message(thread_id=th.id, author_kind="user", author="dieter", body_md=text)
    return th, msg


def _preroute(store: ConversationStore, bus: _Bus, text: str, **overrides: Any) -> tuple[Any, Any]:
    thread, msg = _ask(store, text, kind=overrides.pop("kind", "auto"))
    kwargs: dict[str, Any] = {
        "thread": thread,
        "user_msg": msg,
        "caller_id": "dieter",
        "store": store,
        "bus": bus,
        "known_roles": KNOWN,
    }
    kwargs.update(overrides)
    return thread, asyncio.run(preroute_auto_message(**kwargs))


@pytest.mark.parametrize(
    ("text", "keyword"),
    [
        ("analyse the queue backlog", "analyse"),
        ("please analyze the queue", "analyze"),
        ("I need an analysis of the queue", "analysis"),
        ("investigate why jobs are slow", "investigate"),
        ("diagnose the slow queue", "diagnose"),
        ("what is the root cause of the backlog", "root cause"),
        ("give me a breakdown of queue wait times", "breakdown"),
    ],
)
def test_analysis_keywords_route_confidently_to_maintenance(text: str, keyword: str) -> None:
    decision = route_deterministic(text)

    assert decision is not None
    assert decision.chosen_role == "maintenance"
    assert decision.confidence >= PRE_ROUTE_CONFIDENCE_THRESHOLD
    assert decision.matched_rule is not None
    assert keyword in decision.matched_rule


def test_the_threshold_admits_a_clear_keyword_winner_but_not_a_tie() -> None:
    assert 0.70 < PRE_ROUTE_CONFIDENCE_THRESHOLD <= 0.85


def test_a_clear_keyword_winner_is_confident() -> None:
    decision = confident_route("analyse the queue backlog", KNOWN)

    assert decision is not None
    assert decision.chosen_role == "maintenance"


def test_a_keyword_tie_is_not_confident() -> None:
    assert confident_route("analyse issue #12 for me", KNOWN) is None


def test_a_message_with_no_rule_is_not_confident() -> None:
    assert confident_route("hello, how is your day going?", KNOWN) is None


def test_barb_topics_stay_with_barb() -> None:
    assert confident_route("what are the priorities today?", KNOWN) is None


def test_a_role_that_is_not_loaded_is_not_confident() -> None:
    assert confident_route("analyse the queue backlog", frozenset({"barb"})) is None


def test_an_explicit_mention_of_a_loaded_role_is_confident() -> None:
    decision = confident_route("@librarian tidy the style guide", KNOWN)

    assert decision is not None
    assert decision.chosen_role == "librarian"
    assert decision.matched_rule == "@librarian"


def test_a_confident_message_is_handed_to_the_specialist_with_a_visible_card() -> None:
    store, bus = get_conversation_store(), _Bus()

    thread, pre = _preroute(store, bus, "analyse the queue backlog")

    assert pre is not None
    assert pre.role == "maintenance"
    cards = [m for m in store.list_messages(thread.id) if m.kind == "handoff"]
    assert len(cards) == 1
    assert cards[0].meta["to_role"] == "maintenance"
    assert cards[0].meta["reason"] == 'auto-routed: matched "analyse"'
    assert cards[0].meta["target_thread_id"] == pre.thread_id
    assert (thread.id, "handoff") in [(t, m["kind"]) for t, m in bus.published]
    target = store.get_thread(pre.thread_id)
    assert target is not None
    assert "maintenance" in target.participants


def test_an_ambiguous_message_is_not_prerouted() -> None:
    store, bus = get_conversation_store(), _Bus()

    thread, pre = _preroute(store, bus, "analyse issue #12 for me")

    assert pre is None
    assert [m for m in store.list_messages(thread.id) if m.kind == "handoff"] == []


def test_only_auto_threads_are_prerouted() -> None:
    store, bus = get_conversation_store(), _Bus()

    _, pre = _preroute(store, bus, "analyse the queue backlog", kind="direct")

    assert pre is None


def test_a_specialist_without_budget_leaves_the_message_with_barb() -> None:
    store, bus = get_conversation_store(), _Bus()

    _, pre = _preroute(store, bus, "analyse the queue backlog", can_chat=lambda role: (False, "budget reached"))

    assert pre is None


def test_a_failure_during_preroute_falls_back_to_barb() -> None:
    store, bus = get_conversation_store(), _Bus()

    with patch("staff.chat_preroute.BarbRouter.execute_handoff", side_effect=RuntimeError("boom")):
        thread, pre = _preroute(store, bus, "analyse the queue backlog")

    assert pre is None
    assert [m for m in store.list_messages(thread.id) if m.kind == "handoff"] == []


def test_a_failing_role_loader_falls_back_to_barb() -> None:
    store, bus = get_conversation_store(), _Bus()

    with patch("staff.chat_preroute.load_roles", side_effect=OSError("roles unreadable")):
        _, pre = _preroute(store, bus, "analyse the queue backlog", known_roles=None)

    assert pre is None


# ── Follow-ups after a Barb reply stay with Barb (#1760) ────────────────────


def _barb_reply(store: ConversationStore, thread_id: str) -> None:
    store.add_message(thread_id=thread_id, author_kind="role", author="barb", body_md="Let me look into that.")


def test_a_keyword_follow_up_after_a_barb_reply_is_not_confident() -> None:
    store, bus = get_conversation_store(), _Bus()
    thread, first_msg = _ask(store, "hello, how is your day going?")
    asyncio.run(
        preroute_auto_message(
            thread=thread, user_msg=first_msg, caller_id="dieter", store=store, bus=bus, known_roles=KNOWN
        )
    )
    _barb_reply(store, thread.id)
    follow_up = store.add_message(
        thread_id=thread.id, author_kind="user", author="dieter", body_md="analyse the queue backlog"
    )

    pre = asyncio.run(
        preroute_auto_message(
            thread=thread, user_msg=follow_up, caller_id="dieter", store=store, bus=bus, known_roles=KNOWN
        )
    )

    assert pre is None
    assert [m for m in store.list_messages(thread.id) if m.kind == "handoff"] == []


def test_an_explicit_mention_follow_up_after_a_barb_reply_still_routes() -> None:
    store, bus = get_conversation_store(), _Bus()
    thread, first_msg = _ask(store, "hello, how is your day going?")
    asyncio.run(
        preroute_auto_message(
            thread=thread, user_msg=first_msg, caller_id="dieter", store=store, bus=bus, known_roles=KNOWN
        )
    )
    _barb_reply(store, thread.id)
    follow_up = store.add_message(
        thread_id=thread.id, author_kind="user", author="dieter", body_md="@librarian please tidy this up"
    )

    pre = asyncio.run(
        preroute_auto_message(
            thread=thread, user_msg=follow_up, caller_id="dieter", store=store, bus=bus, known_roles=KNOWN
        )
    )

    assert pre is not None
    assert pre.role == "librarian"


def test_confident_route_with_allow_keyword_false_only_accepts_explicit() -> None:
    assert confident_route("analyse the queue backlog", KNOWN, allow_keyword=False) is None

    decision = confident_route("@librarian tidy the style guide", KNOWN, allow_keyword=False)
    assert decision is not None
    assert decision.chosen_role == "librarian"


def test_confident_route_ignores_keywords_inside_a_pasted_table_row() -> None:
    text = (
        "Here is the decision table:\n\n"
        "| Proposal | Decision |\n"
        "| --- | --- |\n"
        "| Isolate Engine, Recorder, and Analysis State | Take it to the Board of Directors |\n\n"
        "Take it to the Board of Directors."
    )
    assert confident_route(text, KNOWN) is None


def test_confident_route_ignores_keywords_inside_a_blockquote() -> None:
    text = "> please investigate the root cause\n\nJust forwarding that quote along, no action needed."
    assert confident_route(text, KNOWN) is None


def test_confident_route_ignores_keywords_inside_fenced_code() -> None:
    text = "```\ninvestigate()\n```\n\nJust sharing a code sample."
    assert confident_route(text, KNOWN) is None
