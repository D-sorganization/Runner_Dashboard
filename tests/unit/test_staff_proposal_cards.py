"""Proposals become ActionCard messages in their thread (#1547).

A proposed action is its own ``action_proposal`` message whose ``meta.proposal`` is the
card; the proposal's ``message_id`` is that message, so a decision rewrites the card in
place and both reload and SSE show it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.proposal_cards import post_proposal, proposal_card, refresh_proposal_card


class _Bus:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any]]] = []

    async def publish_message(self, thread_id: str, message_dict: dict[str, Any]) -> int:
        self.messages.append((thread_id, message_dict))
        return 1


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    reset_conversation_store()
    s = get_conversation_store()
    yield s
    s.close()
    reset_conversation_store()


def _thread(store: Any) -> str:
    return store.create_thread(title="t", kind="direct", participants=["barb"]).id


@pytest.mark.asyncio
async def test_post_proposal_adds_a_card_message_the_proposal_points_at(store: Any) -> None:
    tid = _thread(store)
    bus = _Bus()

    prop = await post_proposal(
        store,
        bus,
        thread_id=tid,
        author="barb",
        action="runner.restart",
        params={"runner": "runner-1"},
        reason="runner-1 is stalled",
    )

    msg = store.get_message(prop.message_id)
    assert msg is not None
    assert msg.kind == "action_proposal"
    assert (msg.author_kind, msg.author) == ("role", "barb")
    assert msg.body_md == "runner-1 is stalled"
    card = msg.meta["proposal"]
    assert card["id"] == prop.id
    assert card["action_name"] == "runner.restart"
    assert card["params"] == {"runner": "runner-1"}
    assert card["status"] == "pending"
    assert card["proposed_by"] == "barb"
    assert card["description"] == "runner-1 is stalled"
    assert card["risk_level"] == prop.risk
    assert [m["id"] for _, m in bus.messages] == [msg.id]
    assert bus.messages[0][1]["meta"]["proposal"]["id"] == prop.id


@pytest.mark.asyncio
async def test_refresh_rewrites_the_card_after_a_decision(store: Any) -> None:
    tid = _thread(store)
    prop = await post_proposal(
        store, _Bus(), thread_id=tid, author="barb", action="runner.restart", params={}, reason="why"
    )
    store.decide_proposal(prop.id, "denied", decided_by="human:alice", reason="no")
    bus = _Bus()

    await refresh_proposal_card(store, bus, prop.id)

    card = store.get_message(prop.message_id).meta["proposal"]
    assert card["status"] == "denied"
    assert card["decided_by"] == "human:alice"
    assert card["description"] == "why"
    assert bus.messages[0][1]["meta"]["proposal"]["status"] == "denied"


@pytest.mark.asyncio
async def test_refresh_leaves_non_card_messages_alone(store: Any) -> None:
    tid = _thread(store)
    msg = store.add_message(thread_id=tid, author_kind="role", author="maintenance", body_md="Detection")
    prop = store.create_proposal(
        message_id=msg.id, thread_id=tid, action="runner.restart", params={}, risk="medium", principal="m"
    )
    bus = _Bus()

    await refresh_proposal_card(store, bus, prop.id)

    assert store.get_message(msg.id).kind == "text"
    assert bus.messages == []


@pytest.mark.parametrize(
    ("state", "status"),
    [
        ("proposed", "pending"),
        ("approved", "approved"),
        ("executing", "approved"),
        ("denied", "denied"),
        ("done", "executed"),
        ("failed", "failed"),
        ("expired", "expired"),
    ],
)
def test_card_status_follows_the_proposal_state(store: Any, state: str, status: str) -> None:
    tid = _thread(store)
    msg = store.add_message(thread_id=tid, author_kind="role", author="barb", body_md="x")
    prop = store.create_proposal(
        message_id=msg.id, thread_id=tid, action="runner.restart", params={}, risk="low", principal="barb"
    )
    prop.state = state
    assert proposal_card(prop, description="d", proposed_by="barb")["status"] == status
