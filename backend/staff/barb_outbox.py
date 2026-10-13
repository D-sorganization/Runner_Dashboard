"""Bounded, persistent, ordered outbox for non-authority Barb nodes (#1800, BR-06).

A worker that finishes while the authority is unreachable records its run-card update here
(durably, before acknowledging it locally) and a drainer delivers it later. The outbox is a
pending *delivery*, never an authoritative conversation write.

Contract:
* bounded - ``enqueue`` raises :class:`OutboxFullError` rather than exceed ``max_entries`` /
  ``max_bytes``; nothing is silently dropped (only non-terminal run-card updates superseded by a
  newer version of the same run are coalesced, never a terminal one);
* ordered - an entry is handed out only when no lower version of its (kind, thread, run) key is
  pending or in flight;
* crash-safe - ``recover_in_flight`` returns entries that were mid-delivery to ``pending``;
* every entry is delivered, pending or dead (I5); dead entries still occupy the bound.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from staff.barb_db import env_positive_int, immediate_txn

COALESCING_KINDS = frozenset({"run_card"})
DEFAULT_MAX_ENTRIES = 5000
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_ENTRY_BYTES = 256 * 1024
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 300.0


class OutboxFullError(RuntimeError):
    """The outbox is at its bound: apply back-pressure to the producer (503 ``outbox_full``)."""


class OutboxConflictError(ValueError):
    """The same (kind, thread, run, version) key was enqueued again with a different payload (a producer bug)."""


class OutboxEntryTooLarge(ValueError):
    """A single payload exceeds ``max_entry_bytes``."""


@dataclass(frozen=True)
class OutboxEntry:
    outbox_id: str
    kind: str
    thread_id: str
    run_id: str
    version: int
    terminal: bool
    payload: dict[str, Any]
    status: str
    attempts: int
    next_attempt_at: float
    last_error: str


@dataclass(frozen=True)
class OutboxStats:
    entries: int
    bytes: int
    dead: int
    oldest_created_at: float | None


def backoff_seconds(attempts: int, *, rng: random.Random | None = None) -> float:
    """Exponential backoff ``2s * 2^attempts`` capped at 300s, with jitter in ``[0.5x, 1x]``."""
    if attempts < 0:
        raise ValueError("attempts must be >= 0")
    raw = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2 ** min(attempts, 20))
    return raw * (0.5 + 0.5 * (rng or random).random())


def bounds_from_env(environ: Mapping[str, str] | None = None) -> tuple[int, int, int]:
    """``(max_entries, max_bytes, max_entry_bytes)`` from ``BARB_OUTBOX_*`` (defaults 5000 / 64 MiB / 256 KiB).

    Post: raises :class:`~staff.barb_db.BarbConfigError` naming the variable when a value is malformed.
    """
    env = os.environ if environ is None else environ
    return (
        env_positive_int(env, "BARB_OUTBOX_MAX_ENTRIES", DEFAULT_MAX_ENTRIES),
        env_positive_int(env, "BARB_OUTBOX_MAX_BYTES", DEFAULT_MAX_BYTES),
        env_positive_int(env, "BARB_OUTBOX_MAX_ENTRY_BYTES", DEFAULT_MAX_ENTRY_BYTES),
    )


_KEY = "kind = ? AND thread_id = ? AND run_id = ?"


class OutboxStore:
    """SQLite-backed outbox sharing the node's conversation connection (migration 4)."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: threading.RLock,
        *,
        max_entries: int,
        max_bytes: int,
        max_entry_bytes: int,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        if min(max_entries, max_bytes, max_entry_bytes) < 1:
            raise ValueError("outbox bounds must all be >= 1")
        self._conn, self._lock = conn, lock
        self.max_entries, self.max_bytes, self.max_entry_bytes = max_entries, max_bytes, max_entry_bytes
        self.clock = clock
        self._rng = rng or random.Random()

    # ── producer ─────────────────────────────────────────────────────────────
    def enqueue(
        self, kind: str, thread_id: str, run_id: str, version: int, payload: dict[str, Any], *, terminal: bool = False
    ) -> str:
        """Persist one delivery; returns its id (the existing id when the same key and payload were already enqueued).

        Pre: non-empty ``kind``/``thread_id``, ``version >= 1``, payload within ``max_entry_bytes``.
        Post: entries and bytes still within bounds, else :class:`OutboxFullError` and no change. The same key with
        a different payload raises :class:`OutboxConflictError` (the old entry is never silently kept).
        """
        if not kind or not thread_id or version < 1:
            raise ValueError("kind, thread_id and version >= 1 are required")
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        size = len(data.encode("utf-8"))
        if size > self.max_entry_bytes:
            raise OutboxEntryTooLarge(f"payload {size} bytes > max_entry_bytes {self.max_entry_bytes}")
        with immediate_txn(self._conn, self._lock) as conn:
            key = (kind, thread_id, run_id)
            row = conn.execute(
                f"SELECT outbox_id, payload FROM barb_outbox WHERE {_KEY} AND version = ?",  # noqa: S608
                (*key, version),
            ).fetchone()
            if row:
                if row["payload"] != data:
                    raise OutboxConflictError(
                        f"{kind}/{thread_id}/{run_id} v{version} already enqueued with a different payload"
                    )
                return str(row["outbox_id"])
            if kind in COALESCING_KINDS:
                conn.execute(
                    f"DELETE FROM barb_outbox WHERE {_KEY} AND status = 'pending' AND terminal = 0 AND version < ?",  # noqa: S608
                    (*key, version),
                )
            agg = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b FROM barb_outbox WHERE status != 'delivered'"
            ).fetchone()
            if agg["n"] + 1 > self.max_entries or agg["b"] + size > self.max_bytes:
                raise OutboxFullError(
                    f"outbox full: {agg['n']}/{self.max_entries} entries, {agg['b']}/{self.max_bytes} bytes"
                )
            outbox_id = f"obx_{uuid.uuid4().hex[:16]}"
            conn.execute(
                "INSERT INTO barb_outbox (outbox_id, kind, thread_id, run_id, version, terminal, payload, size_bytes, "
                "status, attempts, next_attempt_at, last_error, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 0, '', ?)",
                (outbox_id, kind, thread_id, run_id, version, 1 if terminal else 0, data, size, self.clock()),
            )
            return outbox_id

    # ── drainer ──────────────────────────────────────────────────────────────
    def claim_due(self, *, limit: int, now: float | None = None) -> list[OutboxEntry]:
        """Hand out due heads of line (lowest pending version per key, none in flight) and mark them delivering."""
        at = self.clock() if now is None else now
        with immediate_txn(self._conn, self._lock) as conn:
            rows = conn.execute(
                "SELECT * FROM barb_outbox o WHERE o.status = 'pending' AND o.next_attempt_at <= ? "
                "AND NOT EXISTS (SELECT 1 FROM barb_outbox d WHERE d.kind = o.kind AND d.thread_id = o.thread_id "
                "AND d.run_id = o.run_id AND d.status = 'delivering') "
                "AND NOT EXISTS (SELECT 1 FROM barb_outbox p WHERE p.kind = o.kind AND p.thread_id = o.thread_id "
                "AND p.run_id = o.run_id AND p.status = 'pending' AND p.version < o.version) "
                "ORDER BY o.created_at, o.version LIMIT ?",
                (at, limit),
            ).fetchall()
            for r in rows:
                conn.execute("UPDATE barb_outbox SET status = 'delivering' WHERE outbox_id = ?", (r["outbox_id"],))
        return [self._entry(r, status="delivering") for r in rows]

    def mark_delivered(self, outbox_id: str, *, now: float | None = None) -> None:
        self._set(outbox_id, "status = 'delivered', delivered_at = ?", (self.clock() if now is None else now,))

    def mark_retry(self, outbox_id: str, error: str, *, now: float | None = None) -> None:
        """Back to pending with ``attempts + 1`` and the next attempt after the jittered backoff."""
        at = self.clock() if now is None else now
        with immediate_txn(self._conn, self._lock) as conn:
            row = conn.execute("SELECT attempts FROM barb_outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
            if row is None:
                return
            delay = backoff_seconds(int(row["attempts"]), rng=self._rng)
            conn.execute(
                "UPDATE barb_outbox SET status = 'pending', attempts = attempts + 1, next_attempt_at = ?, "
                "last_error = ? WHERE outbox_id = ?",
                (at + delay, error[:500], outbox_id),
            )

    def mark_dead(self, outbox_id: str, error: str) -> None:
        self._set(outbox_id, "status = 'dead', last_error = ?", (error[:500],))

    def release(self, outbox_ids: list[str]) -> None:
        """Return claimed-but-unsent entries to pending without counting an attempt."""
        for oid in outbox_ids:
            self._set(oid, "status = 'pending'", ())

    def recover_in_flight(self) -> int:
        """Startup recovery: entries left ``delivering`` by a crash become ``pending``; returns how many."""
        with immediate_txn(self._conn, self._lock) as conn:
            return conn.execute("UPDATE barb_outbox SET status = 'pending' WHERE status = 'delivering'").rowcount

    def prune_delivered(self, *, older_than: float) -> int:
        with immediate_txn(self._conn, self._lock) as conn:
            return conn.execute(
                "DELETE FROM barb_outbox WHERE status = 'delivered' AND delivered_at < ?", (older_than,)
            ).rowcount

    # ── inspection ───────────────────────────────────────────────────────────
    def depth(self) -> int:
        """Undelivered entries (pending, delivering and dead)."""
        return self.stats().entries

    def stats(self) -> OutboxStats:
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b, "
                "COALESCE(SUM(status = 'dead'), 0) AS dead, MIN(created_at) AS oldest "
                "FROM barb_outbox WHERE status != 'delivered'"
            ).fetchone()
        return OutboxStats(int(r["n"]), int(r["b"]), int(r["dead"]), r["oldest"])

    def pending_for(self, thread_id: str, run_id: str) -> list[OutboxEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM barb_outbox WHERE thread_id = ? AND run_id = ? "
                "AND status != 'delivered' ORDER BY version",
                (thread_id, run_id),
            ).fetchall()
        return [self._entry(r) for r in rows]

    def dead(self) -> list[OutboxEntry]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM barb_outbox WHERE status = 'dead' ORDER BY created_at").fetchall()
        return [self._entry(r) for r in rows]

    # ── internals ────────────────────────────────────────────────────────────
    def _set(self, outbox_id: str, assignments: str, params: tuple[Any, ...]) -> None:
        with immediate_txn(self._conn, self._lock) as conn:
            conn.execute(f"UPDATE barb_outbox SET {assignments} WHERE outbox_id = ?", (*params, outbox_id))  # noqa: S608

    @staticmethod
    def _entry(r: sqlite3.Row, status: str | None = None) -> OutboxEntry:
        return OutboxEntry(
            outbox_id=r["outbox_id"],
            kind=r["kind"],
            thread_id=r["thread_id"],
            run_id=r["run_id"],
            version=int(r["version"]),
            terminal=bool(r["terminal"]),
            payload=json.loads(r["payload"]),
            status=status or r["status"],
            attempts=int(r["attempts"]),
            next_attempt_at=float(r["next_attempt_at"]),
            last_error=r["last_error"],
        )
