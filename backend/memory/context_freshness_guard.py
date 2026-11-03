"""Freshness and conflict guards over a batch of shared-context entries
(#3938, barb-memory slice 2).

Slice 1 (context_contract.py, #3937) gives per-entry authority/freshness
primitives. This module applies them across a *batch* of entries read for
one Barb shared-context request: it groups entries that describe the same
fact, resolves each group to a single authoritative, non-superseded winner
(suppressing the rest), classifies what remains as fresh/stale/expired, and
memoizes the result over a bounded, TTL-expiring cache so repeated reads of
an unchanged batch within one request cycle don't re-walk every pair.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass

from memory.context_contract import freshness_state, higher_authority
from memory.models import MemoryEntry

FactKey = tuple[str | None, str]


def _fact_key(entry: MemoryEntry) -> FactKey:
    """Group entries the same way service.validate_lesson_acceptance groups
    lesson clashes at write time (repo + topic/rule), generalized across
    entry types: a task, a lesson and a decision can all describe the same
    fact, and resolving which one governs is the point of this guard."""
    topic = str(entry.body.get("topic") or entry.body.get("rule") or "").strip().lower()
    return (entry.repo, topic)


@dataclass(frozen=True)
class ConflictGroup:
    """Two or more entries describing the same fact; only ``winner`` governs."""

    key: FactKey
    winner: MemoryEntry
    suppressed: tuple[MemoryEntry, ...]


@dataclass(frozen=True)
class GuardedContext:
    """The entries a caller may actually serve, after conflict and freshness guards."""

    fresh: tuple[MemoryEntry, ...]
    stale: tuple[MemoryEntry, ...]
    conflicts: tuple[ConflictGroup, ...]


def resolve_conflicts(entries: list[MemoryEntry]) -> tuple[ConflictGroup, ...]:
    """Group *entries* by fact key and resolve each multi-entry group to one winner.

    Entries with no topic/rule proxy never group (nothing to compare), so a
    missing proxy fails open to "no conflict" rather than a false collision.
    """
    groups: dict[FactKey, list[MemoryEntry]] = {}
    for entry in entries:
        key = _fact_key(entry)
        if not key[1]:
            continue
        groups.setdefault(key, []).append(entry)
    resolved: list[ConflictGroup] = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        winner = members[0]
        for member in members[1:]:
            winner = higher_authority(winner, member)
        suppressed = tuple(m for m in members if m.id != winner.id)
        if suppressed:
            resolved.append(ConflictGroup(key=key, winner=winner, suppressed=suppressed))
    return tuple(resolved)


def guard_context(entries: list[MemoryEntry], *, now_iso: str) -> GuardedContext:
    """Apply conflict resolution, then freshness classification, to *entries*.

    A conflict's suppressed losers never appear in ``fresh``/``stale`` even
    if they would otherwise be fresh; the batch exposes conflicts separately
    so a caller can audit or surface them without silently losing data.
    """
    conflicts = resolve_conflicts(entries)
    suppressed_ids = {member.id for group in conflicts for member in group.suppressed}
    fresh: list[MemoryEntry] = []
    stale: list[MemoryEntry] = []
    for entry in entries:
        if entry.id in suppressed_ids:
            continue
        state = freshness_state(entry, now_iso)
        if state == "expired":
            continue
        (fresh if state == "fresh" else stale).append(entry)
    return GuardedContext(fresh=tuple(fresh), stale=tuple(stale), conflicts=conflicts)


def snapshot_key(entries: list[MemoryEntry]) -> tuple[tuple[str, str | None, str, str | None], ...]:
    """The only entry state that can change ``guard_context``'s output for a
    fixed ``now_iso``: id (identity), ``superseded_by`` (authority),
    ``created_at`` (freshness ceiling) and ``expires_at`` (expiry). Sorted so
    the key doesn't depend on caller-supplied ordering."""
    return tuple(sorted((e.id, e.superseded_by, e.created_at, e.expires_at) for e in entries))


_DEFAULT_TTL_SECONDS = 30.0
_DEFAULT_MAX_ENTRIES = 64


class GuardedContextCache:
    """Bounded, TTL-expiring memo for ``guard_context`` over a snapshot key.

    Conflict resolution is O(n^2) in the worst case of fact-key collisions;
    several callers reading the same batch within one request cycle (e.g.
    multiple Barb seats) should reuse one cached result instead of each
    re-walking it. A cache hit requires both an identical entry snapshot
    and an identical ``now_iso`` — this is a same-request memo, not a
    time-bucketed approximation that could paper over a real freshness
    transition.
    """

    def __init__(
        self,
        *,
        compute: Callable[..., GuardedContext] = guard_context,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._compute = compute
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._store: OrderedDict[Hashable, tuple[float, GuardedContext]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._store)

    def get_or_compute(self, entries: list[MemoryEntry], *, now_iso: str) -> GuardedContext:
        key: Hashable = (snapshot_key(entries), now_iso)
        now = self._clock()
        cached = self._store.get(key)
        if cached is not None:
            cached_at, result = cached
            if now - cached_at <= self._ttl:
                self._store.move_to_end(key)
                return result
            del self._store[key]
        result = self._compute(entries, now_iso=now_iso)
        self._store[key] = (now, result)
        self._store.move_to_end(key)
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)
        return result
