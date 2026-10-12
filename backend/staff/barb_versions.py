"""Version-gated, idempotent run-card upsert at the Barb authority (#1800, BR-06).

Contract (the invariants are enforced here and in ``ConversationStore.apply_run_card_update``):

* I2  an ``(run_id, version)`` is applied at most once - a replay is ``DUPLICATE``;
* I3  the stored version never decreases - a lower version is ``STALE`` and mutates nothing;
* a terminal status (succeeded/failed/cancelled) is sticky: a non-terminal update never
  overwrites it, whatever its version; only a strictly higher-version terminal (a retry
  attempt) may replace it;
* the authority epoch must match (a stale sender and a stale authority are both fenced).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from enum import Enum

from staff.store import _now

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
RECEIPT_KIND_RUN_CARD = "run_card"


class ApplyOutcome(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    STALE = "stale"


class StaleEpochError(RuntimeError):
    """The request's authority epoch differs from this authority's (fenced; maps to 409 stale_authority_epoch)."""


class InvariantViolation(AssertionError):
    """A Barb authority invariant was about to be broken (never swallowed)."""


@dataclass(frozen=True)
class ApplyResult:
    outcome: ApplyOutcome
    version: int  # the version stored after this call (the current one for DUPLICATE/STALE)
    receipt_id: str | None = None


def check_epoch(request_epoch: int | None, authority_epoch: int) -> None:
    """Pre: none. Post: raises :class:`StaleEpochError` unless the epochs agree (``None`` = not epoch-aware)."""
    if request_epoch is not None and request_epoch != authority_epoch:
        raise StaleEpochError(f"request epoch {request_epoch} != authority epoch {authority_epoch}")


def receipt_ref(run_id: str, version: int) -> str:
    return f"{run_id}:{version}"


def _legacy_status(conn: sqlite3.Connection, message_id: str) -> str | None:
    """Status of a card written before migration 5 (it has a message but no version row)."""
    row = conn.execute("SELECT meta FROM messages WHERE id = ?", (message_id,)).fetchone()
    if row is None:
        return None
    try:
        meta = json.loads(row["meta"] or "{}")
    except (TypeError, ValueError):
        return None
    status = meta.get("status") if isinstance(meta, dict) else None
    return status if isinstance(status, str) else None


def current_version(conn: sqlite3.Connection, run_id: str) -> int:
    row = conn.execute("SELECT version FROM run_card_versions WHERE run_id = ?", (run_id,)).fetchone()
    return int(row["version"]) if row else 0


def find_receipt(conn: sqlite3.Connection, run_id: str, version: int) -> str | None:
    row = conn.execute(
        "SELECT receipt_id FROM barb_receipts WHERE kind = ? AND ref_id = ?",
        (RECEIPT_KIND_RUN_CARD, receipt_ref(run_id, version)),
    ).fetchone()
    return str(row["receipt_id"]) if row else None


def decide(conn: sqlite3.Connection, *, run_id: str, version: int, status: str, message_id: str) -> ApplyResult:
    """Classify an update against the stored state without writing. Caller holds the transaction."""
    row = conn.execute("SELECT version, status FROM run_card_versions WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        stored_version, stored_status = 0, _legacy_status(conn, message_id)
        known = False
    else:
        stored_version, stored_status, known = int(row["version"]), str(row["status"]), True

    if known and version == stored_version:
        return ApplyResult(ApplyOutcome.DUPLICATE, stored_version, find_receipt(conn, run_id, version))
    if stored_status in TERMINAL_STATUSES:
        if status in TERMINAL_STATUSES and version > stored_version:
            return ApplyResult(ApplyOutcome.APPLIED, version)
        return ApplyResult(ApplyOutcome.STALE, stored_version)
    if version > stored_version:
        return ApplyResult(ApplyOutcome.APPLIED, version)
    return ApplyResult(ApplyOutcome.STALE, stored_version)


def record_applied(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    version: int,
    status: str,
    message_id: str,
    thread_id: str,
    seq: int,
    epoch: int,
) -> str:
    """Persist the version row and the receipt for an applied update; returns the receipt id.

    Post: the stored version is exactly ``version`` and strictly higher than before (I3), else
    :class:`InvariantViolation` (the surrounding transaction then rolls back).
    """
    now = _now()
    cur = conn.execute(
        "INSERT INTO run_card_versions (run_id, version, status, message_id, thread_id, epoch, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_id) DO UPDATE SET version = excluded.version, status = excluded.status, "
        "message_id = excluded.message_id, thread_id = excluded.thread_id, epoch = excluded.epoch, "
        "updated_at = excluded.updated_at WHERE excluded.version > run_card_versions.version",
        (run_id, version, status, message_id, thread_id, epoch, now),
    )
    if cur.rowcount != 1:
        raise InvariantViolation(f"run {run_id}: version {version} did not advance the stored version")
    receipt_id = f"rcpt_{uuid.uuid4().hex[:16]}"
    conn.execute(
        "INSERT INTO barb_receipts (receipt_id, kind, ref_id, thread_id, seq, epoch, committed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (receipt_id, RECEIPT_KIND_RUN_CARD, receipt_ref(run_id, version), thread_id, seq, epoch, now),
    )
    return receipt_id
