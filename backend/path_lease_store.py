"""SQLite WAL-backed TTL path/issue lease store (#3931 S1, refs #3878).

Clean-room: built from public docs/behavior descriptions only (no source read
or copied from any external project). Gives a fast, local, atomic lease
primitive keyed by ``(repo, scope)`` (a path glob or ``issue:<n>``), with a
TTL -- separate from the slower GitHub-issue lease wrapped by
``backend/coordination/claims.py``. Used by Cursor dispatch's pre-launch/
pre-publish re-check hook (S2); WAL pattern mirrors ``runner_lease_sqlite``.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

_SQLITE_BUSY_RETRIES = 8
_SQLITE_BUSY_SLEEP_S = 0.05
_T = TypeVar("_T")
_COLUMNS = "lease_id, repo, scope, owner, acquired_at, expires_at"


def default_path_lease_db_path() -> Path:
    override = os.environ.get("DASHBOARD_PATH_LEASE_DB_PATH", "").strip()
    return (
        Path(override).expanduser()
        if override
        else Path(os.environ.get("DASHBOARD_STATE_DIR", "state")) / "path_leases.sqlite3"
    )


class LeaseConflictError(RuntimeError):
    """Another owner holds the lease, or the caller's lease id/ownership is stale."""


@dataclass(frozen=True)
class PathLeaseRecord:
    lease_id: str
    repo: str
    scope: str
    owner: str
    acquired_at: float
    expires_at: float


class PathLeaseStore:
    """SQLite WAL-backed lease store for ``(repo, scope)`` TTL leases."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=30.0)
        self._execute("PRAGMA busy_timeout=30000")
        self._execute("PRAGMA journal_mode=WAL")
        self._execute(
            "CREATE TABLE IF NOT EXISTS path_leases ("
            "  lease_id TEXT PRIMARY KEY, repo TEXT NOT NULL, scope TEXT NOT NULL, owner TEXT NOT NULL,"
            "  acquired_at REAL NOT NULL, expires_at REAL NOT NULL, UNIQUE (repo, scope)"
            ")"
        )

    # -- low-level helpers ---------------------------------------------------

    def _retry_sqlite_busy(self, operation: Callable[[], _T]) -> _T:
        for attempt in range(_SQLITE_BUSY_RETRIES):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == _SQLITE_BUSY_RETRIES - 1:
                    raise
                time.sleep(_SQLITE_BUSY_SLEEP_S * (2**attempt))
        raise AssertionError("unreachable")

    def _execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self._retry_sqlite_busy(lambda: self._db.execute(sql, parameters))

    def _in_transaction(self, body: Callable[[], _T]) -> _T:
        """Run *body* under ``BEGIN IMMEDIATE``, committing on success and rolling back on any error."""

        def _mutate() -> _T:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                result = body()
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")
            return result

        return self._retry_sqlite_busy(_mutate)

    @staticmethod
    def _row_to_record(row: tuple[Any, ...]) -> PathLeaseRecord:
        return PathLeaseRecord(*row)

    def _locked_lease(self, lease_id: str, owner: str, *, require_active: bool) -> PathLeaseRecord:
        """SELECT a lease by id (inside the caller's open transaction), validating ownership and liveness."""
        row = self._db.execute(f"SELECT {_COLUMNS} FROM path_leases WHERE lease_id = ?", (lease_id,)).fetchone()
        if row is None:
            raise LeaseConflictError(f"no lease with id {lease_id!r}")
        rec = self._row_to_record(row)
        if rec.owner != owner or (require_active and rec.expires_at <= time.time()):
            raise LeaseConflictError(f"lease {lease_id!r} is not held by {owner!r}")
        return rec

    # -- reads ----------------------------------------------------------------

    def prune_expired(self) -> None:
        """Delete every lease whose TTL has passed."""
        self._execute("DELETE FROM path_leases WHERE expires_at <= ?", (time.time(),))

    def read_all(self) -> list[PathLeaseRecord]:
        """Active (non-expired) leases, pruning expired rows first."""
        self.prune_expired()
        rows = self._execute(f"SELECT {_COLUMNS} FROM path_leases").fetchall()
        return [self._row_to_record(row) for row in rows]

    def check(self, repo: str, scope: str) -> PathLeaseRecord | None:
        """The active lease on ``(repo, scope)``, or ``None`` if free/expired."""
        self.prune_expired()
        row = self._execute(
            f"SELECT {_COLUMNS} FROM path_leases WHERE repo = ? AND scope = ?", (repo, scope)
        ).fetchone()
        return self._row_to_record(row) if row is not None else None

    # -- writes ----------------------------------------------------------------

    def acquire(self, repo: str, scope: str, owner: str, ttl_seconds: float) -> PathLeaseRecord:
        """Atomically lease ``(repo, scope)`` for *owner*.

        Idempotent renew when *owner* already holds it; otherwise raises
        ``LeaseConflictError`` (409) while another owner's lease is active.
        """

        def _body() -> PathLeaseRecord:
            now = time.time()
            self._db.execute(
                "DELETE FROM path_leases WHERE repo = ? AND scope = ? AND expires_at <= ?", (repo, scope, now)
            )
            existing = self._db.execute(
                f"SELECT {_COLUMNS} FROM path_leases WHERE repo = ? AND scope = ?", (repo, scope)
            ).fetchone()
            if existing is not None:
                rec = self._row_to_record(existing)
                if rec.owner != owner:
                    raise LeaseConflictError(f"{repo}:{scope} is already leased by {rec.owner!r}")
                lease_id, acquired_at = rec.lease_id, rec.acquired_at
            else:
                lease_id, acquired_at = str(uuid.uuid4()), now
            expires_at = now + ttl_seconds
            self._db.execute(
                "INSERT INTO path_leases (lease_id, repo, scope, owner, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (repo, scope) DO UPDATE SET expires_at = excluded.expires_at",
                (lease_id, repo, scope, owner, acquired_at, expires_at),
            )
            return PathLeaseRecord(lease_id, repo, scope, owner, acquired_at, expires_at)

        return self._in_transaction(_body)

    def renew(self, lease_id: str, owner: str, ttl_seconds: float) -> PathLeaseRecord:
        """Extend an active lease's TTL. Raises ``LeaseConflictError`` if it is
        missing, expired, or held by a different owner."""

        def _body() -> PathLeaseRecord:
            rec = self._locked_lease(lease_id, owner, require_active=True)
            expires_at = time.time() + ttl_seconds
            self._db.execute("UPDATE path_leases SET expires_at = ? WHERE lease_id = ?", (expires_at, lease_id))
            return PathLeaseRecord(rec.lease_id, rec.repo, rec.scope, rec.owner, rec.acquired_at, expires_at)

        return self._in_transaction(_body)

    def release(self, lease_id: str, owner: str) -> None:
        """Free the lease. Raises ``LeaseConflictError`` if it is missing or
        held by a different owner (expired leases may still be released)."""

        def _body() -> None:
            self._locked_lease(lease_id, owner, require_active=False)
            self._db.execute("DELETE FROM path_leases WHERE lease_id = ?", (lease_id,))

        self._in_transaction(_body)
