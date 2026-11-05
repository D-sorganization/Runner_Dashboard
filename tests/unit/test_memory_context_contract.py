"""Shared context contract (#3937 slice 1): authority, freshness, privacy,
and read-before-act rules.

Later barb-memory slices (freshness/conflict guards, the rebuildable context
brief, notification pointers) depend on these pure rules rather than each
re-deriving them.
"""

from __future__ import annotations

import pytest
from memory.context_contract import (
    DEFAULT_FRESH_SECONDS,
    DEFAULT_MAX_STALENESS_SECONDS,
    ReadBeforeActError,
    ReadReceipt,
    SharedContextPrivacyError,
    assert_entry_privacy_safe,
    authority_rank,
    freshness_state,
    higher_authority,
    is_authoritative,
    require_read_before_act,
)
from memory.models import MemoryEntry

pytestmark = pytest.mark.unit


def _entry(**kwargs: object) -> MemoryEntry:
    base: dict[str, object] = {
        "id": "mem_1",
        "entry_type": "task",
        "author_principal": "agent:claude-a",
        "source_product": "codex",
        "tags": [],
        "body": {"summary": "fleet checkpoint"},
        "created_at": "2026-11-01T00:00:00+00:00",
    }
    base.update(kwargs)
    return MemoryEntry(**base)  # type: ignore[arg-type]


# --- Authority --------------------------------------------------------------


def test_authority_rank_orders_decision_above_lesson_above_task() -> None:
    assert authority_rank("decision") > authority_rank("lesson") > authority_rank("task")


def test_authority_rank_unknown_entry_type_ranks_lowest() -> None:
    assert authority_rank("transcript") < authority_rank("session_digest")


def test_superseded_entry_is_not_authoritative() -> None:
    live = _entry(id="mem_2", superseded_by=None)
    dead = _entry(id="mem_1", superseded_by="mem_2")
    assert is_authoritative(live) is True
    assert is_authoritative(dead) is False


def test_higher_authority_prefers_live_entry_over_superseded() -> None:
    dead = _entry(id="mem_1", entry_type="decision", superseded_by="mem_2")
    live = _entry(id="mem_2", entry_type="task")
    assert higher_authority(dead, live) is live
    assert higher_authority(live, dead) is live


def test_higher_authority_prefers_higher_ranked_entry_type() -> None:
    task = _entry(id="mem_1", entry_type="task")
    decision = _entry(id="mem_2", entry_type="decision")
    assert higher_authority(task, decision) is decision
    assert higher_authority(decision, task) is decision


def test_higher_authority_breaks_ties_on_recency() -> None:
    older = _entry(id="mem_1", entry_type="task", created_at="2026-11-01T00:00:00+00:00")
    newer = _entry(id="mem_2", entry_type="task", created_at="2026-11-02T00:00:00+00:00")
    assert higher_authority(older, newer) is newer


# --- Freshness ---------------------------------------------------------------


def test_fresh_entry_within_its_ceiling() -> None:
    entry = _entry(entry_type="task", created_at="2026-11-03T00:00:00+00:00")
    now = "2026-11-03T01:00:00+00:00"
    assert freshness_state(entry, now) == "fresh"


def test_stale_entry_past_its_ceiling_but_not_expired() -> None:
    entry = _entry(entry_type="task", created_at="2026-11-01T00:00:00+00:00")
    now = "2026-11-05T00:00:00+00:00"
    assert DEFAULT_FRESH_SECONDS["task"] < (4 * 24 * 3600)
    assert freshness_state(entry, now) == "stale"


def test_expired_entry_per_explicit_expires_at() -> None:
    entry = _entry(entry_type="lesson", created_at="2026-11-01T00:00:00+00:00", expires_at="2026-11-02T00:00:00+00:00")
    now = "2026-11-03T00:00:00+00:00"
    assert freshness_state(entry, now) == "expired"


def test_superseded_entry_is_expired_regardless_of_age() -> None:
    entry = _entry(entry_type="decision", created_at="2026-11-03T00:00:00+00:00", superseded_by="mem_2")
    now = "2026-11-03T00:05:00+00:00"
    assert freshness_state(entry, now) == "expired"


def test_entry_type_without_a_ceiling_stays_fresh_until_expiry() -> None:
    entry = _entry(entry_type="decision", created_at="2020-01-01T00:00:00+00:00")
    now = "2026-11-03T00:00:00+00:00"
    assert "decision" not in DEFAULT_FRESH_SECONDS
    assert freshness_state(entry, now) == "fresh"


def test_naive_timestamps_fail_closed_to_expired() -> None:
    entry = _entry(entry_type="task", created_at="2026-11-03T00:00:00")
    now = "2026-11-03T01:00:00+00:00"
    assert freshness_state(entry, now) == "expired"


# --- Privacy -----------------------------------------------------------------


def test_privacy_safe_entry_passes() -> None:
    entry = _entry(source_product="codex", body={"summary": "ordinary fleet update"})
    assert_entry_privacy_safe(entry)  # does not raise


def test_marge_owned_entry_is_rejected_as_privacy_unsafe() -> None:
    entry = _entry(source_product="gmail", body={"summary": "personal mail"})
    with pytest.raises(SharedContextPrivacyError):
        assert_entry_privacy_safe(entry)


# --- Read-before-act -----------------------------------------------------------


def test_read_before_act_requires_a_receipt() -> None:
    with pytest.raises(ReadBeforeActError):
        require_read_before_act(None, touches=frozenset({"mem_1"}), now_iso="2026-11-03T00:00:00+00:00")


def test_read_before_act_requires_every_touched_id_to_have_been_read() -> None:
    receipt = ReadReceipt(read_at="2026-11-03T00:00:00+00:00", entry_ids=frozenset({"mem_1"}))
    with pytest.raises(ReadBeforeActError):
        require_read_before_act(
            receipt, touches=frozenset({"mem_1", "mem_2"}), now_iso="2026-11-03T00:00:01+00:00"
        )


def test_read_before_act_rejects_a_stale_receipt() -> None:
    receipt = ReadReceipt(read_at="2026-11-03T00:00:00+00:00", entry_ids=frozenset({"mem_1"}))
    now = "2026-11-03T00:10:00+00:00"  # 600s later, default ceiling is 300s
    with pytest.raises(ReadBeforeActError):
        require_read_before_act(receipt, touches=frozenset({"mem_1"}), now_iso=now)


def test_read_before_act_accepts_a_fresh_full_receipt() -> None:
    receipt = ReadReceipt(read_at="2026-11-03T00:00:00+00:00", entry_ids=frozenset({"mem_1", "mem_2"}))
    now = "2026-11-03T00:01:00+00:00"
    require_read_before_act(receipt, touches=frozenset({"mem_1"}), now_iso=now)  # does not raise


def test_read_before_act_rejects_naive_timestamps() -> None:
    receipt = ReadReceipt(read_at="2026-11-03T00:00:00", entry_ids=frozenset({"mem_1"}))
    with pytest.raises(ReadBeforeActError):
        require_read_before_act(receipt, touches=frozenset({"mem_1"}), now_iso="2026-11-03T00:00:01+00:00")


def test_custom_max_staleness_is_honored() -> None:
    receipt = ReadReceipt(read_at="2026-11-03T00:00:00+00:00", entry_ids=frozenset({"mem_1"}))
    now = "2026-11-03T00:00:30+00:00"
    assert DEFAULT_MAX_STALENESS_SECONDS > 30
    with pytest.raises(ReadBeforeActError):
        require_read_before_act(receipt, touches=frozenset({"mem_1"}), now_iso=now, max_staleness_seconds=10)
