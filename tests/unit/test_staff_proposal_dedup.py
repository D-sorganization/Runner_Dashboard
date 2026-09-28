"""Unit tests for proposal deduplication and chat replay visibility (#1716).

Defects addressed:
- Relayed yes files a duplicate proposal for a run that already has a pending card.
- Pending proposals were omitted from history replay, so the assistant could not
  name the pending card or know it was already pending.
- Barb claims dispatching when it cannot execute or approve proposals in chat.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from staff.chat_history import build_pending_proposals_block, format_history_replay
from staff.conversation_proposals import create_proposal, decide_proposal, find_pending_proposal
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
    reset_conversation_store,
)
from staff.proposal_cards import CARD_KIND, post_proposal
from staff.roles import RoleSpec


class _MockBus:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any]]] = []

    async def publish_message(self, thread_id: str, message_dict: dict[str, Any]) -> int:
        self.messages.append((thread_id, message_dict))
        return 1


@pytest.fixture
def isolated_db(tmp_path: Path) -> tuple[sqlite3.Connection, threading.RLock]:
    db_file = tmp_path / "isolated.sqlite3"
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    from staff.conversation_migrations import run_migrations

    run_migrations(conn, db_file)
    lock = threading.RLock()
    return conn, lock


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ConversationStore]:
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    s = get_conversation_store(path=db_file)
    yield s
    s.close()
    reset_conversation_store()


@pytest.fixture
def barb_role() -> RoleSpec:
    return RoleSpec(
        name="barb",
        title="Barb",
        summary="Personal secretary.",
        instructions="Handle secretary tasks and answer operator questions.",
    )


def test_find_pending_proposal(isolated_db: tuple[sqlite3.Connection, threading.RLock]) -> None:
    conn, lock = isolated_db
    # None when empty
    assert find_pending_proposal(conn, lock, "th_1", "staff.dispatch", {"role": "cartographer"}) is None

    p1 = create_proposal(
        conn,
        lock,
        message_id="msg_1",
        thread_id="th_1",
        action="staff.dispatch",
        params={"role": "cartographer", "repo": "UpstreamDrift"},
    )
    # Exact match finds it
    found = find_pending_proposal(
        conn, lock, "th_1", "staff.dispatch", {"role": "cartographer", "repo": "UpstreamDrift"}
    )
    assert found is not None
    assert found.id == p1.id

    # Different thread or params does not match
    assert (
        find_pending_proposal(conn, lock, "th_2", "staff.dispatch", {"role": "cartographer", "repo": "UpstreamDrift"})
        is None
    )
    assert find_pending_proposal(conn, lock, "th_1", "staff.dispatch", {"role": "issue-remediator"}) is None


def test_create_proposal_deduplicates_pending_in_thread(
    isolated_db: tuple[sqlite3.Connection, threading.RLock],
) -> None:
    conn, lock = isolated_db
    p1 = create_proposal(
        conn,
        lock,
        message_id="msg_1",
        thread_id="th_1",
        action="staff.dispatch",
        params={"role": "cartographer"},
        deduplicate=True,
    )

    # Calling create_proposal again with identical action and params returns p1
    p2 = create_proposal(
        conn,
        lock,
        message_id="msg_2",
        thread_id="th_1",
        action="staff.dispatch",
        params={"role": "cartographer"},
        deduplicate=True,
    )
    assert p2.id == p1.id

    # Verify only 1 row in DB
    rows = conn.execute("SELECT COUNT(*) as cnt FROM action_proposals WHERE thread_id = 'th_1'").fetchone()
    assert rows["cnt"] == 1


def test_create_proposal_does_not_deduplicate_decided_proposal(
    isolated_db: tuple[sqlite3.Connection, threading.RLock],
) -> None:
    conn, lock = isolated_db
    p1 = create_proposal(
        conn,
        lock,
        message_id="msg_1",
        thread_id="th_1",
        action="staff.dispatch",
        params={"role": "cartographer"},
        deduplicate=True,
    )
    decide_proposal(conn, lock, p1.id, "approved", decided_by="human:dieter")

    # Now that p1 is approved, a new proposal with the same params creates a fresh proposal
    p2 = create_proposal(
        conn,
        lock,
        message_id="msg_2",
        thread_id="th_1",
        action="staff.dispatch",
        params={"role": "cartographer"},
        deduplicate=True,
    )
    assert p2.id != p1.id
    rows = conn.execute("SELECT COUNT(*) as cnt FROM action_proposals WHERE thread_id = 'th_1'").fetchone()
    assert rows["cnt"] == 2


@pytest.mark.asyncio
async def test_post_proposal_deduplicates_and_avoids_duplicate_card(store: ConversationStore) -> None:
    th = store.create_thread(title="Test", kind="direct", participants=["barb", "user"])
    bus = _MockBus()

    # First post creates card message and proposal
    prop1 = await post_proposal(
        store,
        bus,
        thread_id=th.id,
        author="barb",
        action="staff.dispatch",
        params={"role": "cartographer"},
        reason="Dispatch cartographer",
    )
    assert len(bus.messages) == 1
    assert len(store.list_messages(th.id)) == 1

    # Second post with identical action and params returns prop1 without posting new card
    prop2 = await post_proposal(
        store,
        bus,
        thread_id=th.id,
        author="barb",
        action="staff.dispatch",
        params={"role": "cartographer"},
        reason="Dispatch cartographer again",
    )
    assert prop2.id == prop1.id
    # Message count in thread must remain 1 (no second card)
    assert len(store.list_messages(th.id)) == 1
    # No second message published to bus
    assert len(bus.messages) == 1


@pytest.mark.unit
def test_chat_history_replay_includes_action_proposals(
    store: ConversationStore,
    barb_role: RoleSpec,
) -> None:
    th = store.create_thread(title="Grok Relay Thread", kind="direct", participants=["barb", "agent-grok"])

    # User asks for run
    store.add_message(
        thread_id=th.id,
        author_kind="user",
        author="agent-grok",
        kind="text",
        body_md="Can you propose a read-only run for cartographer?",
    )

    # Barb text reply
    store.add_message(
        thread_id=th.id,
        author_kind="role",
        author="barb",
        kind="text",
        body_md="I have proposed that run.",
    )

    # Action card proposal message
    store.add_message(
        thread_id=th.id,
        author_kind="role",
        author="barb",
        kind=CARD_KIND,
        body_md="Dispatch cartographer on UpstreamDrift",
        meta={
            "proposal": {
                "id": "prop_c967f09fcf16",
                "action_name": "staff.dispatch",
                "status": "pending",
                "description": "Dispatch cartographer on UpstreamDrift",
            }
        },
    )

    # Format replay for next turn (where user relays: yes, please dispatch)
    replay = format_history_replay(
        conv_store=store,
        thread_id=th.id,
        current_prompt="yes, please dispatch",
        role=barb_role,
    )

    # Action proposal must be present in the replay output
    assert "prop_c967f09fcf16" in replay
    assert "staff.dispatch" in replay
    assert "pending" in replay


def test_build_pending_proposals_block(store: ConversationStore) -> None:
    th = store.create_thread(title="Test Pending Block", kind="direct", participants=["barb", "user"])
    # No proposals initially
    assert build_pending_proposals_block(store, th.id) is None

    # Add a pending proposal
    prop = store.create_proposal(
        message_id="msg_1",
        thread_id=th.id,
        action="staff.dispatch",
        params={"role": "cartographer", "repo": "UpstreamDrift"},
        risk="low",
    )
    block = build_pending_proposals_block(store, th.id)
    assert block is not None
    assert prop.id in block
    assert "staff.dispatch" in block
    assert "Staff Console" in block

    # After deciding the proposal, block becomes None again
    store.decide_proposal(prop.id, "approved", decided_by="human:dieter")
    assert build_pending_proposals_block(store, th.id) is None
