"""Idempotency store and request gating for Staff API v1 (SC-F3, Issue #1312).

Specifications:
- Idempotency-Key header on every mutating POST: same key within 24 h returns the original result.
- Replaying with the same key does not execute a second time.
- Idempotency store failure -> 503 with retryable: true rather than risking duplicates.
- A key is reserved atomically before any effect runs (BR-01, #1795): concurrent requests with
  the same key execute once, a reused key with a different payload is refused, and a
  reservation whose outcome was never recorded is reported as unknown rather than re-run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import Header, HTTPException
from staff.store import _now, default_db_path, first_touch_lock

log = logging.getLogger("dashboard.staff.idempotency")

DEFAULT_TTL_HOURS = 24
# How long a reservation may stay pending before its outcome is treated as unknown.
DEFAULT_LEASE_SECONDS = 600

ReservationState = Literal["acquired", "replay", "in_progress", "mismatch", "unknown_outcome"]

# Columns added for atomic reservation (#1795); legacy rows are completed receipts.
_RESERVATION_COLUMNS = {
    "state": "TEXT NOT NULL DEFAULT 'done'",
    "payload_hash": "TEXT NOT NULL DEFAULT ''",
    "operation_id": "TEXT NOT NULL DEFAULT ''",
    "lease_expires_at": "TEXT NOT NULL DEFAULT ''",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    principal TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    response_headers TEXT NOT NULL,
    response_body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (key, endpoint, principal)
);
CREATE INDEX IF NOT EXISTS idx_idempotency_expires ON idempotency_keys(expires_at);
"""


class IdempotencyStoreError(RuntimeError):
    """Raised when the idempotency backing store fails."""


@dataclass
class IdempotencyRecord:
    """Cached response for an idempotent operation."""

    key: str
    endpoint: str
    principal: str
    status_code: int
    response_headers: dict[str, str]
    response_body: str
    created_at: str
    expires_at: str


@dataclass
class Reservation:
    """Result of reserving a key before running its effect.

    ``acquired``: the caller owns the operation and must ``complete`` or ``release`` it.
    ``replay``: the operation finished; ``record`` holds its receipt.
    ``in_progress``: another request holds a live reservation.
    ``mismatch``: the key was used with a different payload.
    ``unknown_outcome``: a reservation lapsed without a recorded result.
    """

    state: ReservationState
    operation_id: str
    record: IdempotencyRecord | None = None
    recovered: bool = False


def payload_fingerprint(payload: Any) -> str:
    """SHA-256 of the payload's canonical JSON (sorted keys, compact separators)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _iso_in(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _record_from_row(row: sqlite3.Row) -> IdempotencyRecord:
    headers = json.loads(row["response_headers"]) if row["response_headers"] else {}
    return IdempotencyRecord(
        key=row["key"],
        endpoint=row["endpoint"],
        principal=row["principal"],
        status_code=int(row["status_code"]),
        response_headers=headers,
        response_body=str(row["response_body"]),
        created_at=str(row["created_at"]),
        expires_at=str(row["expires_at"]),
    )


class IdempotencyStore:
    """Thread-safe SQLite store for 24h idempotency keys."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=30.0)
            self._conn.row_factory = sqlite3.Row
            with first_touch_lock(self.path), self._lock:
                self._conn.execute("PRAGMA busy_timeout = 30000")
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.executescript(_SCHEMA)
                self._migrate()
        except Exception as exc:
            log.error("Failed to initialize idempotency store: %s", exc)
            raise IdempotencyStoreError(str(exc)) from exc

    def _migrate(self) -> None:
        """Add the reservation columns to a ledger created before #1795 (additive only)."""
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(idempotency_keys)")}
        for name, decl in _RESERVATION_COLUMNS.items():
            if name not in have:
                self._conn.execute(f"ALTER TABLE idempotency_keys ADD COLUMN {name} {decl}")

    def reserve(
        self,
        key: str,
        endpoint: str,
        principal: str,
        payload_hash: str,
        *,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        takeover_safe: bool = False,
    ) -> Reservation:
        """Atomically claim (key, endpoint, principal) before running its effect.

        Runs in one ``BEGIN IMMEDIATE`` transaction, which holds SQLite's write lock,
        so reservations on different connections or processes are serialised.
        ``takeover_safe`` lets a lapsed reservation be resumed under the same operation
        id; use it only for operations that are safe to repeat.
        """
        now_iso = _now()
        try:
            with self._lock:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    result = self._reserve_locked(
                        key, endpoint, principal, payload_hash, now_iso, lease_seconds, takeover_safe
                    )
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise
                self._conn.execute("COMMIT")
                return result
        except Exception as exc:
            log.error("Idempotency reserve error: %s", exc)
            raise IdempotencyStoreError(f"Idempotency reservation failed: {exc}") from exc

    def _reserve_locked(
        self,
        key: str,
        endpoint: str,
        principal: str,
        payload_hash: str,
        now_iso: str,
        lease_seconds: float,
        takeover_safe: bool,
    ) -> Reservation:
        ident = (key, endpoint, principal)
        self._conn.execute(
            "DELETE FROM idempotency_keys WHERE key = ? AND endpoint = ? AND principal = ? AND expires_at <= ?",
            (*ident, now_iso),
        )
        operation_id = f"op-{uuid.uuid4().hex[:12]}"
        cur = self._conn.execute(
            """
            INSERT INTO idempotency_keys
            (key, endpoint, principal, status_code, response_headers, response_body, created_at, expires_at,
             state, payload_hash, operation_id, lease_expires_at)
            VALUES (?, ?, ?, 0, '{}', '', ?, ?, 'pending', ?, ?, ?)
            ON CONFLICT(key, endpoint, principal) DO NOTHING
            """,
            (*ident, now_iso, _iso_in(DEFAULT_TTL_HOURS * 3600), payload_hash, operation_id, _iso_in(lease_seconds)),
        )
        if cur.rowcount == 1:
            return Reservation("acquired", operation_id)

        row = self._conn.execute(
            "SELECT * FROM idempotency_keys WHERE key = ? AND endpoint = ? AND principal = ?", ident
        ).fetchone()
        existing_op = str(row["operation_id"])
        if row["payload_hash"] and payload_hash and row["payload_hash"] != payload_hash:
            return Reservation("mismatch", existing_op)
        if row["state"] == "done":
            return Reservation("replay", existing_op, record=_record_from_row(row))
        if row["lease_expires_at"] > now_iso:
            return Reservation("in_progress", existing_op)
        if takeover_safe:
            self._conn.execute(
                "UPDATE idempotency_keys SET lease_expires_at = ? WHERE key = ? AND endpoint = ? AND principal = ?",
                (_iso_in(lease_seconds), *ident),
            )
            return Reservation("acquired", existing_op, recovered=True)
        return Reservation("unknown_outcome", existing_op)

    def complete(
        self,
        key: str,
        endpoint: str,
        principal: str,
        status_code: int,
        response_headers: dict[str, str],
        response_body: str,
        ttl_seconds: int | None = None,
    ) -> None:
        """Record the final receipt of a reservation this caller acquired."""
        ttl = ttl_seconds if ttl_seconds is not None else DEFAULT_TTL_HOURS * 3600
        skip_headers = ("content-length", "transfer-encoding")
        clean_headers = {k: v for k, v in response_headers.items() if k.lower() not in skip_headers}
        try:
            with self._lock:
                self._conn.execute(
                    """
                    UPDATE idempotency_keys
                    SET state = 'done', status_code = ?, response_headers = ?, response_body = ?, expires_at = ?
                    WHERE key = ? AND endpoint = ? AND principal = ?
                    """,
                    (status_code, json.dumps(clean_headers), response_body, _iso_in(ttl), key, endpoint, principal),
                )
        except Exception as exc:
            log.error("Idempotency complete error: %s", exc)
            raise IdempotencyStoreError(f"Idempotency completion failed: {exc}") from exc

    def release(self, key: str, endpoint: str, principal: str) -> None:
        """Drop a pending reservation whose action failed before any effect."""
        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM idempotency_keys"
                    " WHERE key = ? AND endpoint = ? AND principal = ? AND state = 'pending'",
                    (key, endpoint, principal),
                )
        except Exception as exc:
            log.error("Idempotency release error: %s", exc)
            raise IdempotencyStoreError(f"Idempotency release failed: {exc}") from exc

    def get(self, key: str, endpoint: str, principal: str) -> IdempotencyRecord | None:
        """Fetch the unexpired completed response for (key, endpoint, principal)."""
        now_iso = _now()
        try:
            with self._lock:
                row = self._conn.execute(
                    """
                    SELECT * FROM idempotency_keys
                    WHERE key = ? AND endpoint = ? AND principal = ? AND expires_at > ? AND state = 'done'
                    """,
                    (key, endpoint, principal, now_iso),
                ).fetchone()
            if not row:
                return None
            return _record_from_row(row)
        except Exception as exc:
            log.error("Idempotency get error: %s", exc)
            raise IdempotencyStoreError(f"Idempotency lookup failed: {exc}") from exc

    def save(
        self,
        key: str,
        endpoint: str,
        principal: str,
        status_code: int,
        response_headers: dict[str, str],
        response_body: str,
        ttl_hours: int = DEFAULT_TTL_HOURS,
        ttl_seconds: int | None = None,
    ) -> IdempotencyRecord:
        """Store a completed response under (key, endpoint, principal)."""
        created = _now()
        if ttl_seconds is not None:
            expires = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z")
        else:
            expires = (datetime.now(UTC) + timedelta(hours=ttl_hours)).isoformat().replace("+00:00", "Z")
        skip_headers = ("content-length", "transfer-encoding")
        clean_headers = {k: v for k, v in response_headers.items() if k.lower() not in skip_headers}
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO idempotency_keys
                    (key, endpoint, principal, status_code, response_headers, response_body, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key, endpoint, principal) DO UPDATE SET
                        status_code = excluded.status_code,
                        response_headers = excluded.response_headers,
                        response_body = excluded.response_body,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at,
                        state = 'done'
                    """,
                    (
                        key,
                        endpoint,
                        principal,
                        status_code,
                        json.dumps(clean_headers),
                        response_body,
                        created,
                        expires,
                    ),
                )
            return IdempotencyRecord(
                key=key,
                endpoint=endpoint,
                principal=principal,
                status_code=status_code,
                response_headers=clean_headers,
                response_body=response_body,
                created_at=created,
                expires_at=expires,
            )
        except Exception as exc:
            log.error("Idempotency save error: %s", exc)
            raise IdempotencyStoreError(f"Idempotency save failed: {exc}") from exc

    def purge_expired(self) -> int:
        """Clean up keys that expired prior to now."""
        now_iso = _now()
        with self._lock:
            cur = self._conn.execute("DELETE FROM idempotency_keys WHERE expires_at <= ?", (now_iso,))
            return cur.rowcount

    def prune_expired(self) -> int:
        """Alias for purge_expired."""
        return self.purge_expired()


_GLOBAL_IDEMPOTENCY_STORE: IdempotencyStore | None = None
_STORE_LOCK = threading.RLock()


def get_idempotency_store(path: Path | None = None) -> IdempotencyStore:
    global _GLOBAL_IDEMPOTENCY_STORE
    with _STORE_LOCK:
        if _GLOBAL_IDEMPOTENCY_STORE is None or (path is not None and _GLOBAL_IDEMPOTENCY_STORE.path != path):
            _GLOBAL_IDEMPOTENCY_STORE = IdempotencyStore(path=path)
        return _GLOBAL_IDEMPOTENCY_STORE


def reset_idempotency_store() -> None:
    global _GLOBAL_IDEMPOTENCY_STORE
    with _STORE_LOCK:
        _GLOBAL_IDEMPOTENCY_STORE = None


def require_idempotency_header(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    """FastAPI dependency enforcing presence of the Idempotency-Key header."""
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "missing_idempotency_key",
                "message": "Idempotency-Key header is required for mutating staff operations",
                "retryable": False,
                "hint": "Provide a unique UUID or client-generated token in the Idempotency-Key header.",
            },
        )
    return idempotency_key.strip()
