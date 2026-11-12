"""Freshness and conflict guards over a batch of shared-context entries
(#3938, barb-memory slice 2).

Builds on the pure authority/freshness primitives in context_contract.py
(#3937 slice 1) rather than re-deriving them.
"""

from __future__ import annotations

import pytest
from memory.context_freshness_guard import (
    ConflictGroup,
    GuardedContext,
    GuardedContextCache,
    guard_context,
    resolve_conflicts,
    snapshot_key,
)
from memory.models import MemoryEntry

pytestmark = pytest.mark.unit

NOW = "2026-11-03T12:00:00+00:00"


def _entry(**kwargs: object) -> MemoryEntry:
    base: dict[str, object] = {
        "id": "mem_1",
        "entry_type": "task",
        "author_principal": "agent:claude-a",
        "source_product": "codex",
        "tags": [],
        "body": {"summary": "fleet checkpoint"},
        "created_at": "2026-11-03T11:00:00+00:00",
    }
    base.update(kwargs)
    return MemoryEntry(**base)  # type: ignore[arg-type]


# --- resolve_conflicts -------------------------------------------------------


def test_no_conflict_when_only_one_entry_shares_a_fact_key() -> None:
    entries = [_entry(id="mem_1", entry_type="lesson", repo="RD", body={"topic": "arming cap", "stance": "a"})]
    assert resolve_conflicts(entries) == ()


def test_entries_without_topic_or_rule_never_conflict_group() -> None:
    entries = [
        _entry(id="mem_1", entry_type="task", body={"summary": "a"}),
        _entry(id="mem_2", entry_type="task", body={"summary": "b"}),
    ]
    assert resolve_conflicts(entries) == ()


def test_same_fact_key_different_entry_type_rank_resolves_to_higher_rank() -> None:
    lesson = _entry(
        id="mem_1",
        entry_type="lesson",
        repo="RD",
        body={"topic": "arming cap", "stance": "wait"},
        created_at="2026-11-03T10:00:00+00:00",
    )
    decision = _entry(
        id="mem_2",
        entry_type="decision",
        repo="RD",
        body={"topic": "arming cap", "stance": "proceed"},
        created_at="2026-11-03T09:00:00+00:00",
    )
    groups = resolve_conflicts([lesson, decision])
    assert len(groups) == 1
    group = groups[0]
    assert isinstance(group, ConflictGroup)
    assert group.winner.id == "mem_2"
    assert group.suppressed == (lesson,)


def test_same_fact_key_same_rank_breaks_tie_on_newer_created_at() -> None:
    older = _entry(id="mem_1", entry_type="task", repo="RD", body={"topic": "x"}, created_at="2026-11-03T08:00:00+00:00")
    newer = _entry(id="mem_2", entry_type="task", repo="RD", body={"topic": "x"}, created_at="2026-11-03T09:00:00+00:00")
    group = resolve_conflicts([older, newer])[0]
    assert group.winner.id == "mem_2"


def test_superseded_entry_never_wins_a_conflict_even_if_higher_rank() -> None:
    superseded_decision = _entry(
        id="mem_1", entry_type="decision", repo="RD", body={"topic": "x"}, superseded_by="mem_9"
    )
    live_task = _entry(id="mem_2", entry_type="task", repo="RD", body={"topic": "x"})
    group = resolve_conflicts([superseded_decision, live_task])[0]
    assert group.winner.id == "mem_2"


def test_different_repo_same_topic_does_not_conflict() -> None:
    a = _entry(id="mem_1", entry_type="lesson", repo="RD", body={"topic": "x"})
    b = _entry(id="mem_2", entry_type="lesson", repo="GM", body={"topic": "x"})
    assert resolve_conflicts([a, b]) == ()


def test_three_way_conflict_picks_single_overall_winner() -> None:
    a = _entry(id="mem_1", entry_type="task", repo="RD", body={"topic": "x"}, created_at="2026-11-03T08:00:00+00:00")
    b = _entry(id="mem_2", entry_type="lesson", repo="RD", body={"topic": "x"}, created_at="2026-11-03T08:00:00+00:00")
    c = _entry(id="mem_3", entry_type="decision", repo="RD", body={"topic": "x"}, created_at="2026-11-03T08:00:00+00:00")
    group = resolve_conflicts([a, b, c])[0]
    assert group.winner.id == "mem_3"
    assert {e.id for e in group.suppressed} == {"mem_1", "mem_2"}


# --- guard_context ------------------------------------------------------------


def test_guard_context_classifies_fresh_entry() -> None:
    result = guard_context([_entry(entry_type="session_digest", created_at=NOW)], now_iso=NOW)
    assert isinstance(result, GuardedContext)
    assert len(result.fresh) == 1
    assert result.stale == ()


def test_guard_context_classifies_stale_entry() -> None:
    old = _entry(entry_type="session_digest", created_at="2026-11-01T00:00:00+00:00")
    result = guard_context([old], now_iso=NOW)
    assert result.fresh == ()
    assert len(result.stale) == 1


def test_guard_context_drops_expired_entry_entirely() -> None:
    expired = _entry(expires_at="2026-11-01T00:00:00+00:00")
    result = guard_context([expired], now_iso=NOW)
    assert result.fresh == ()
    assert result.stale == ()


def test_guard_context_excludes_suppressed_conflict_losers_from_fresh_and_stale() -> None:
    lesson = _entry(id="mem_1", entry_type="lesson", repo="RD", body={"topic": "x", "stance": "a"}, created_at=NOW)
    decision = _entry(id="mem_2", entry_type="decision", repo="RD", body={"topic": "x"}, created_at=NOW)
    result = guard_context([lesson, decision], now_iso=NOW)
    ids = {e.id for e in (*result.fresh, *result.stale)}
    assert ids == {"mem_2"}
    assert len(result.conflicts) == 1


def test_guard_context_reports_conflicts_even_when_winner_is_stale() -> None:
    old_decision = _entry(
        id="mem_2", entry_type="decision", repo="RD", body={"topic": "x"}, created_at="2026-11-01T00:00:00+00:00"
    )
    old_lesson = _entry(
        id="mem_1", entry_type="lesson", repo="RD", body={"topic": "x"}, created_at="2026-11-01T00:00:00+00:00"
    )
    result = guard_context([old_decision, old_lesson], now_iso=NOW)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].winner.id == "mem_2"


# --- GuardedContextCache -------------------------------------------------------


def test_cache_hits_on_identical_snapshot_and_now_without_recomputing() -> None:
    calls = 0

    def spy(entries: list[MemoryEntry], *, now_iso: str) -> GuardedContext:
        nonlocal calls
        calls += 1
        return guard_context(entries, now_iso=now_iso)

    cache = GuardedContextCache(compute=spy)
    entries = [_entry(created_at=NOW)]
    first = cache.get_or_compute(entries, now_iso=NOW)
    second = cache.get_or_compute(entries, now_iso=NOW)
    assert first == second
    assert calls == 1


def test_cache_misses_when_entries_change() -> None:
    calls = 0

    def spy(entries: list[MemoryEntry], *, now_iso: str) -> GuardedContext:
        nonlocal calls
        calls += 1
        return guard_context(entries, now_iso=now_iso)

    cache = GuardedContextCache(compute=spy)
    cache.get_or_compute([_entry(id="mem_1", created_at=NOW)], now_iso=NOW)
    cache.get_or_compute([_entry(id="mem_2", created_at=NOW)], now_iso=NOW)
    assert calls == 2


def test_cache_misses_once_ttl_expires() -> None:
    calls = 0
    fake_time = [1000.0]

    def spy(entries: list[MemoryEntry], *, now_iso: str) -> GuardedContext:
        nonlocal calls
        calls += 1
        return guard_context(entries, now_iso=now_iso)

    cache = GuardedContextCache(compute=spy, ttl_seconds=10, clock=lambda: fake_time[0])
    entries = [_entry(created_at=NOW)]
    cache.get_or_compute(entries, now_iso=NOW)
    fake_time[0] += 11
    cache.get_or_compute(entries, now_iso=NOW)
    assert calls == 2


def test_cache_evicts_oldest_entry_past_max_size() -> None:
    cache = GuardedContextCache(compute=guard_context, max_entries=2)
    for i in range(3):
        cache.get_or_compute([_entry(id=f"mem_{i}", created_at=NOW)], now_iso=NOW)
    assert len(cache) == 2


def test_snapshot_key_changes_when_an_entry_is_superseded() -> None:
    live = _entry(id="mem_1", superseded_by=None)
    dead = _entry(id="mem_1", superseded_by="mem_2")
    assert snapshot_key([live]) != snapshot_key([dead])


def test_snapshot_key_is_order_independent() -> None:
    a = _entry(id="mem_1")
    b = _entry(id="mem_2")
    assert snapshot_key([a, b]) == snapshot_key([b, a])
