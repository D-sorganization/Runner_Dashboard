"""Shared context contract (#3937): authority, freshness, privacy, and
read-before-act rules for Barb's shared assistant memory.

Later barb-memory slices (freshness/conflict guards, the rebuildable context
brief, notification pointers) build on these pure, side-effect-free rules
instead of each re-deriving authority/freshness/privacy semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from memory.barb_shared_guard import entry_allowed_in_shared_memory
from memory.expiry import expiry_is_future
from memory.models import EntryType, MemoryEntry

# --- Authority ---------------------------------------------------------------
# An entry is authoritative only while nothing has superseded it (the existing
# CAS chain, store.append_idempotent). Among authoritative entries describing
# the same fact, higher-ranked entry types outrank lower ones; ties break on
# the newer created_at. Entry types absent here rank lowest.
AUTHORITY_RANK: dict[EntryType, int] = {
    "decision": 3,
    "lesson": 2,
    "task": 1,
    "session_digest": 0,
}


def is_authoritative(entry: MemoryEntry) -> bool:
    """False once another entry has superseded *entry* via the CAS chain."""
    return entry.superseded_by is None


def authority_rank(entry_type: EntryType) -> int:
    return AUTHORITY_RANK.get(entry_type, -1)


def higher_authority(a: MemoryEntry, b: MemoryEntry) -> MemoryEntry:
    """Pick which of two conflicting, same-fact entries governs.

    A superseded entry never wins. Among authoritative entries, higher-ranked
    entry types win; ties break on the newer ``created_at``.
    """
    a_live, b_live = is_authoritative(a), is_authoritative(b)
    if a_live != b_live:
        return a if a_live else b
    rank_a, rank_b = authority_rank(a.entry_type), authority_rank(b.entry_type)
    if rank_a != rank_b:
        return a if rank_a > rank_b else b
    return a if a.created_at >= b.created_at else b


# --- Freshness ----------------------------------------------------------------
# Advisory ceilings: a live (unexpired, unsuperseded) entry past its ceiling
# is still usable but must be surfaced as "stale", never silently treated as
# current. Entry types absent here have no advisory ceiling (fresh until
# explicitly expired or superseded).
FreshnessState = Literal["fresh", "stale", "expired"]

DEFAULT_FRESH_SECONDS: dict[EntryType, int] = {
    "task": 3 * 24 * 3600,
    "session_digest": 24 * 3600,
}


def freshness_state(entry: MemoryEntry, now_iso: str) -> FreshnessState:
    """Classify *entry* as fresh, stale, or expired as of *now_iso*.

    Fails closed to "expired" on a superseded entry, an explicit expiry in
    the past, or a timestamp this contract cannot safely compare (missing
    timezone, unparsable).
    """
    if not is_authoritative(entry) or not expiry_is_future(entry.expires_at, now_iso):
        return "expired"
    ceiling = DEFAULT_FRESH_SECONDS.get(entry.entry_type)
    if ceiling is None:
        return "fresh"
    try:
        created, now = datetime.fromisoformat(entry.created_at), datetime.fromisoformat(now_iso)
    except (TypeError, ValueError):
        return "expired"
    if created.utcoffset() is None or now.utcoffset() is None:
        return "expired"
    return "fresh" if (now - created).total_seconds() <= ceiling else "stale"


# --- Privacy -------------------------------------------------------------------
# Privacy is enforced once, at read time, by memory.barb_shared_guard (Marge
# exclusion + rd-barb hold lint, #3936) and memory.service's caller-scoped
# data-class filtering (#3547). This contract does not re-derive those rules;
# it gives later slices one call to make so a second, drifting set of privacy
# checks never gets written.
class SharedContextPrivacyError(Exception):
    """An entry fails the shared-context privacy gate."""


def assert_entry_privacy_safe(entry: MemoryEntry) -> None:
    """Raise :class:`SharedContextPrivacyError` if *entry* must not be served
    from shared context."""
    if not entry_allowed_in_shared_memory(entry):
        raise SharedContextPrivacyError(f"entry {entry.id} is not privacy-safe for shared context")


# --- Read-before-act ------------------------------------------------------------
class ReadBeforeActError(Exception):
    """An action touched shared-context state without a fresh-enough prior read."""


@dataclass(frozen=True)
class ReadReceipt:
    """Proof that a caller fetched specific shared-context entries recently."""

    read_at: str
    entry_ids: frozenset[str]


DEFAULT_MAX_STALENESS_SECONDS = 300


def require_read_before_act(
    receipt: ReadReceipt | None,
    *,
    touches: frozenset[str],
    now_iso: str,
    max_staleness_seconds: int = DEFAULT_MAX_STALENESS_SECONDS,
) -> None:
    """Fail closed unless *receipt* covers every id in *touches* and is recent.

    Any action (a merge, an arm, a dispatch) that depends on shared-context
    state must prove it read that state first; a missing or stale receipt is
    a bug in the caller to fix, not something to degrade gracefully around.
    """
    if receipt is None:
        raise ReadBeforeActError("no shared-context read receipt before acting")
    missing = touches - receipt.entry_ids
    if missing:
        raise ReadBeforeActError(f"acted on entries never read: {sorted(missing)}")
    try:
        read_at, now = datetime.fromisoformat(receipt.read_at), datetime.fromisoformat(now_iso)
    except (TypeError, ValueError) as exc:
        raise ReadBeforeActError("invalid read-before-act timestamp") from exc
    if read_at.utcoffset() is None or now.utcoffset() is None:
        raise ReadBeforeActError("naive timestamp in read-before-act check")
    if (now - read_at).total_seconds() > max_staleness_seconds:
        raise ReadBeforeActError("shared-context read is stale")
