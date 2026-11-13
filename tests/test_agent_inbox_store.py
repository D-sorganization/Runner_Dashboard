"""Contract tests for the per-agent inbox store (#3931 S3).

Clean-room: built from public docs/behavior descriptions only (no source
read or copied). Mirrors the ``PathLeaseStore`` WAL pattern (busy-retry, WAL
journal) for a new domain: messages addressed to one agent, optionally
requiring an acknowledgement before a deadline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_inbox_store import AgentInboxStore, InboxConflictError, alert_overdue_acks


def _make_store(tmp_path: Path) -> AgentInboxStore:
    return AgentInboxStore(tmp_path / "agent_inbox.db")


def test_send_then_list_for_recipient(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    msg = store.send("codex-g", "claude-a", "please review PR #1", requires_ack=False, ack_deadline_seconds=None)
    assert msg.to_agent == "codex-g"
    assert msg.from_agent == "claude-a"
    assert msg.body == "please review PR #1"
    assert msg.message_id
    assert msg.acked_at is None

    inbox = store.list_for("codex-g")
    assert [m.message_id for m in inbox] == [msg.message_id]
    assert store.list_for("claude-a") == []


def test_ack_marks_message_acked_and_is_excluded_by_default(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    msg = store.send("codex-g", "claude-a", "ack this", requires_ack=True, ack_deadline_seconds=3600)
    acked = store.ack(msg.message_id, "codex-g")
    assert acked.acked_at is not None

    assert store.list_for("codex-g") == []
    assert [m.message_id for m in store.list_for("codex-g", include_acked=True)] == [msg.message_id]


def test_ack_unknown_message_raises_conflict(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    with pytest.raises(InboxConflictError):
        store.ack("not-a-real-id", "codex-g")


def test_ack_by_wrong_agent_raises_conflict(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    msg = store.send("codex-g", "claude-a", "ack this", requires_ack=True, ack_deadline_seconds=3600)
    with pytest.raises(InboxConflictError):
        store.ack(msg.message_id, "grok-bot")


def test_overdue_lists_only_unacked_past_deadline(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    now = 1_000_000.0

    overdue_msg = store.send(
        "codex-g", "claude-a", "overdue one", requires_ack=True, ack_deadline_seconds=60, now=now - 120
    )
    store.send("codex-g", "claude-a", "not yet due", requires_ack=True, ack_deadline_seconds=3600, now=now - 120)
    not_required = store.send(
        "codex-g", "claude-a", "fyi", requires_ack=False, ack_deadline_seconds=None, now=now - 120
    )
    acked_msg = store.send(
        "codex-g", "claude-a", "already acked", requires_ack=True, ack_deadline_seconds=1, now=now - 120
    )
    store.ack(acked_msg.message_id, "codex-g")

    overdue_ids = {m.message_id for m in store.overdue(now=now)}
    assert overdue_ids == {overdue_msg.message_id}
    assert not_required.message_id not in overdue_ids


def test_store_is_durable_across_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "agent_inbox.db"
    AgentInboxStore(db_path).send("codex-g", "claude-a", "hi", requires_ack=False, ack_deadline_seconds=None)
    reopened = AgentInboxStore(db_path)
    assert len(reopened.list_for("codex-g")) == 1


def test_alert_overdue_acks_sends_one_alert_per_message(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    now = 1_000_000.0
    store.send("codex-g", "claude-a", "overdue one", requires_ack=True, ack_deadline_seconds=1, now=now - 120)
    store.send("grok-bot", "claude-a", "overdue two", requires_ack=True, ack_deadline_seconds=1, now=now - 120)

    sent = []
    count = alert_overdue_acks(store.overdue(now=now), send=sent.append)

    assert count == 2
    assert {m.to_agent for m in sent} == {"codex-g", "grok-bot"}


def test_alert_overdue_acks_is_noop_when_nothing_overdue() -> None:
    sent = []
    count = alert_overdue_acks([], send=sent.append)
    assert count == 0
    assert sent == []
