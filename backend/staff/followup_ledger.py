"""Durable follow-up state for Barb's sweep (BR-04, Issue #1798).

The follow-up engine kept its debounce, history and counters in process dictionaries, so a
restart forgot them and two sweep workers acted on the same item. This ledger keeps them in
the work-item database:

- ``followup_checks`` holds one row per target with ``next_check_at``. :meth:`claim` moves it
  forward with a single conditional UPSERT, so exactly one worker wins a due target and a
  restarted engine still honours the interval.
- ``followup_records`` is the durable history; the daily digest counts actions from it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff.store import first_touch_lock

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS followup_checks (
        target_id TEXT PRIMARY KEY,
        target_kind TEXT NOT NULL,
        next_check_at TEXT NOT NULL,
        claimed_by TEXT NOT NULL DEFAULT '',
        last_action TEXT NOT NULL DEFAULT '',
        last_action_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS followup_records (
        id TEXT PRIMARY KEY,
        target_id TEXT NOT NULL,
        target_kind TEXT NOT NULL,
        condition TEXT NOT NULL,
        action_taken TEXT NOT NULL,
        detail_json TEXT NOT NULL DEFAULT '{}',
        timestamp TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_followup_records_target ON followup_records(target_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_followup_records_ts ON followup_records(timestamp)",
)


def stamp(dt: datetime) -> str:
    """Fixed-width UTC timestamp, so claim times compare correctly as strings."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(slots=True)
class SweepBacklog:
    """What one sweep saw: active items, overdue work and decisions, the oldest due item, and duration."""

    active: int = 0
    overdue: int = 0
    oldest_due_at: str | None = None
    oldest_due_age_seconds: int | None = None
    decisions_overdue: int = 0
    duration_ms: int = 0

    def observe(self, expected_by: str | None, now_iso: str, now: datetime) -> None:
        """Count one active item; items arrive earliest deadline first, so the first overdue is the oldest."""
        self.active += 1
        if not expected_by or expected_by >= now_iso:
            return
        self.overdue += 1
        if self.oldest_due_at is None:
            self.oldest_due_at = expected_by
            due = datetime.fromisoformat(expected_by.replace("Z", "+00:00"))
            due = due if due.tzinfo else due.replace(tzinfo=UTC)
            self.oldest_due_age_seconds = max(0, int((now - due).total_seconds()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    """One persisted follow-up action (mirrors ``staff.followup.FollowupRecord``)."""

    id: str
    target_id: str
    target_kind: str
    condition: str
    action_taken: str
    detail: dict[str, Any]
    timestamp: str


class FollowupLedger:
    """SQLite-backed follow-up claims and history, shared by every sweep worker on the node."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        with first_touch_lock(db_path), self._lock:
            conn = self._conn()
            try:
                for stmt in _SCHEMA:
                    conn.execute(stmt)
                conn.commit()
            finally:
                conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def claim(self, target_id: str, target_kind: str, now_iso: str, next_iso: str, worker: str) -> bool:
        """Take ``target_id`` until ``next_iso`` if its check is due at ``now_iso``.

        Pre: ``now_iso <= next_iso`` (both UTC ISO-8601 with the same format).
        Post: returns True for exactly one caller per due window, across threads and processes.
        """
        assert now_iso <= next_iso, "a claim must move the next check forward"  # noqa: S101
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO followup_checks (target_id, target_kind, next_check_at, claimed_by)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT(target_id) DO UPDATE SET"
                    " next_check_at = excluded.next_check_at, claimed_by = excluded.claimed_by"
                    " WHERE followup_checks.next_check_at <= ?",
                    (target_id, target_kind, next_iso, worker, now_iso),
                )
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    def release(self, target_id: str, now_iso: str, worker: str) -> None:
        """Hand a claimed target back when the check took no action, so the next sweep re-checks it."""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE followup_checks SET next_check_at = ? WHERE target_id = ? AND claimed_by = ?",
                    (now_iso, target_id, worker),
                )
                conn.commit()
            finally:
                conn.close()

    def is_due(self, target_id: str, now_iso: str) -> bool:
        """True when no claim holds ``target_id`` past ``now_iso``."""
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT next_check_at FROM followup_checks WHERE target_id = ?", (target_id,)
                ).fetchone()
            finally:
                conn.close()
        return row is None or str(row["next_check_at"]) <= now_iso

    def record(self, rec: LedgerRecord) -> None:
        """Persist ``rec`` and note it as the target's last action."""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO followup_records (id, target_id, target_kind, condition, action_taken,"
                    " detail_json, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        rec.id,
                        rec.target_id,
                        rec.target_kind,
                        rec.condition,
                        rec.action_taken,
                        json.dumps(rec.detail, default=str),
                        rec.timestamp,
                    ),
                )
                conn.execute(
                    "UPDATE followup_checks SET last_action = ?, last_action_at = ? WHERE target_id = ?",
                    (rec.action_taken, rec.timestamp, rec.target_id),
                )
                conn.commit()
            finally:
                conn.close()

    def records_for(self, target_id: str) -> list[LedgerRecord]:
        """Every recorded follow-up for ``target_id``, oldest first."""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM followup_records WHERE target_id = ? ORDER BY timestamp, id", (target_id,)
                ).fetchall()
            finally:
                conn.close()
        return [
            LedgerRecord(
                r["id"],
                r["target_id"],
                r["target_kind"],
                r["condition"],
                r["action_taken"],
                json.loads(r["detail_json"] or "{}"),
                r["timestamp"],
            )
            for r in rows
        ]

    def action_counts(self, since_iso: str) -> dict[str, int]:
        """Number of follow-up actions of each kind recorded at or after ``since_iso``."""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT action_taken, COUNT(*) AS n FROM followup_records WHERE timestamp >= ?"
                    " GROUP BY action_taken",
                    (since_iso,),
                ).fetchall()
            finally:
                conn.close()
        return {str(r["action_taken"]): int(r["n"]) for r in rows}
