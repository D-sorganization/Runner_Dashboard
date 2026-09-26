"""Idempotency store and request gating for Staff API v1 (SC-F3, Issue #1312).

Specifications:
- Idempotency-Key header on every mutating POST: same key within 24 h returns the original result.
- Replaying with the same key does not execute a second time.
- Idempotency store failure -> 503 with retryable: true rather than risking duplicates.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Header, HTTPException
from staff.store import _now, default_db_path

log = logging.getLogger("dashboard.staff.idempotency")

DEFAULT_TTL_HOURS = 24

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


class IdempotencyStore:
    """Thread-safe SQLite store for 24h idempotency keys."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=30.0)
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout = 30000")
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.executescript(_SCHEMA)
        except Exception as exc:
            log.error("Failed to initialize idempotency store: %s", exc)
            raise IdempotencyStoreError(str(exc)) from exc

    def get(self, key: str, endpoint: str, principal: str) -> IdempotencyRecord | None:
        """Fetch unexpired cached response for (key, endpoint, principal)."""
        now_iso = _now()
        try:
            with self._lock:
                row = self._conn.execute(
                    """
                    SELECT * FROM idempotency_keys
                    WHERE key = ? AND endpoint = ? AND principal = ? AND expires_at > ?
                    """,
                    (key, endpoint, principal, now_iso),
                ).fetchone()
            if not row:
                return None
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
                        expires_at = excluded.expires_at
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
