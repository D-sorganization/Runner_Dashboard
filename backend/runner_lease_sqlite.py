"""SQLite WAL-backed runner lease store (#3878).

Implements the ``runner_lease_migration.LeaseStore`` protocol (``read_all``/
``write_all``) so this store can act as the secondary backend during the
dual-read/drain/rollback cutover (#3877), plus its own ownership/quota/
fencing contract mirroring ``runner_lease.LeaseManager``, backed by SQLite
WAL instead of YAML+fcntl (same pattern as ``backend/replay_store.py``).

Spoke/staging pilot only (#3874); no production cutover without a separately
recorded sign-off, and this module never flips authority itself -- that is
``runner_lease_migration.LeaseMigrationHarness``'s job.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from runner_lease import LeaseRecord

log = logging.getLogger("dashboard.runner_lease_sqlite")

_SQLITE_BUSY_RETRIES = 8
_SQLITE_BUSY_SLEEP_S = 0.05
_T = TypeVar("_T")


class StaleFencingTokenError(RuntimeError):
    """A caller's fencing token no longer matches the lease's current one.

    Signals the lease was renewed since the caller last observed it; its
    view is stale and it must not mutate the lease.
    """


class SqliteLeaseStore:
    """SQLite WAL-backed lease store satisfying the ``LeaseStore`` protocol."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db: sqlite3.Connection = sqlite3.connect(
            str(path),
            isolation_level=None,
            check_same_thread=False,
            timeout=30.0,
        )
        self._execute("PRAGMA busy_timeout=30000")
        self._execute("PRAGMA journal_mode=WAL")
        self._execute(
            "CREATE TABLE IF NOT EXISTS leases ("
            "  runner_id TEXT PRIMARY KEY,"
            "  principal_id TEXT NOT NULL,"
            "  acquired_at REAL NOT NULL,"
            "  expires_at REAL,"
            "  task_id TEXT,"
            "  metadata TEXT NOT NULL DEFAULT '{}',"
            "  fencing_token INTEGER NOT NULL DEFAULT 0"
            ")"
        )
        self._execute("CREATE INDEX IF NOT EXISTS idx_leases_principal ON leases (principal_id)")

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

    @staticmethod
    def _row_to_record(row: tuple[Any, ...]) -> LeaseRecord:
        runner_id, principal_id, acquired_at, expires_at, task_id, metadata_json, fencing_token = row
        return LeaseRecord(
            principal_id=principal_id,
            runner_id=runner_id,
            acquired_at=acquired_at,
            expires_at=expires_at,
            task_id=task_id,
            metadata=json.loads(metadata_json),
            fencing_token=fencing_token,
        )

    # -- LeaseStore protocol --------------------------------------------------

    def read_all(self) -> list[LeaseRecord]:
        """Active (non-expired) leases, pruning expired rows first."""
        self.prune_expired()
        rows = self._execute(
            "SELECT runner_id, principal_id, acquired_at, expires_at, task_id, metadata, fencing_token FROM leases"
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def write_all(self, records: list[LeaseRecord]) -> None:
        """Replace the store's entire contents with *records* (drain/cutover).

        Each record starts a fresh fencing-token sequence at 0, mirroring
        ``YamlLeaseStore.write_all``'s snapshot-replace semantics.
        """

        def _mutate() -> None:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute("DELETE FROM leases")
                for rec in records:
                    self._db.execute(
                        "INSERT INTO leases "
                        "(runner_id, principal_id, acquired_at, expires_at, task_id, metadata, fencing_token) "
                        "VALUES (?, ?, ?, ?, ?, ?, 0)",
                        (
                            rec.runner_id,
                            rec.principal_id,
                            rec.acquired_at,
                            rec.expires_at,
                            rec.task_id,
                            json.dumps(rec.metadata or {}),
                        ),
                    )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

        self._retry_sqlite_busy(_mutate)

    # -- ownership / quota / fencing contract --------------------------------

    def acquire(
        self,
        principal_id: str,
        runner_id: str,
        max_runners: int,
        duration_seconds: int = 3600,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LeaseRecord:
        """Acquire or renew a lease, enforcing per-principal quotas.

        Idempotent for the same principal (renews, bumping the fencing
        token). Raises ``ValueError`` if another principal already holds
        *runner_id*, or ``PermissionError`` at the ``max_runners`` quota.
        Runs inside one ``BEGIN IMMEDIATE`` transaction so concurrent
        acquirers cannot both observe room under quota and both insert
        (the SQLite-backend equivalent of #327/#936's YAML-store fix).
        """
        now = time.time()
        expires_at = now + duration_seconds

        def _mutate() -> LeaseRecord:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute("DELETE FROM leases WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,))

                existing = self._db.execute(
                    "SELECT principal_id, acquired_at, task_id, metadata, fencing_token "
                    "FROM leases WHERE runner_id = ?",
                    (runner_id,),
                ).fetchone()
                if existing is not None:
                    existing_principal, acquired_at, existing_task_id, existing_metadata_json, fencing_token = existing
                    if existing_principal != principal_id:
                        self._db.execute("ROLLBACK")
                        raise ValueError(f"Runner {runner_id} is already leased by {existing_principal}")
                    merged_metadata = {**json.loads(existing_metadata_json), **(metadata or {})}
                    new_task_id = task_id or existing_task_id
                    new_fencing_token = fencing_token + 1
                    self._db.execute(
                        "UPDATE leases SET expires_at = ?, task_id = ?, metadata = ?, fencing_token = ? "
                        "WHERE runner_id = ?",
                        (expires_at, new_task_id, json.dumps(merged_metadata), new_fencing_token, runner_id),
                    )
                    self._db.execute("COMMIT")
                    log.info(
                        "sqlite lease UPDATED principal=%s runner=%s task=%s", principal_id, runner_id, new_task_id
                    )
                    return LeaseRecord(
                        principal_id=principal_id,
                        runner_id=runner_id,
                        acquired_at=acquired_at,
                        expires_at=expires_at,
                        task_id=new_task_id,
                        metadata=merged_metadata,
                        fencing_token=new_fencing_token,
                    )

                (active_count,) = self._db.execute(
                    "SELECT COUNT(*) FROM leases WHERE principal_id = ?", (principal_id,)
                ).fetchone()
                if active_count >= max_runners:
                    self._db.execute("ROLLBACK")
                    raise PermissionError(f"Principal {principal_id} has reached runner quota ({max_runners})")

                self._db.execute(
                    "INSERT INTO leases "
                    "(runner_id, principal_id, acquired_at, expires_at, task_id, metadata, fencing_token) "
                    "VALUES (?, ?, ?, ?, ?, ?, 0)",
                    (runner_id, principal_id, now, expires_at, task_id, json.dumps(metadata or {})),
                )
                self._db.execute("COMMIT")
                log.info("sqlite lease ACQUIRED principal=%s runner=%s task=%s", principal_id, runner_id, task_id)
                return LeaseRecord(
                    principal_id=principal_id,
                    runner_id=runner_id,
                    acquired_at=now,
                    expires_at=expires_at,
                    # runner_lease's mypy override (strict_optional=false)
                    # bakes task_id's exported type as plain ``str`` for
                    # importers checked under strict_optional=true; it is
                    # genuinely Optional at runtime.
                    task_id=task_id,  # type: ignore[arg-type]
                    metadata=metadata or {},
                    fencing_token=0,
                )
            except (ValueError, PermissionError):
                raise
            except Exception:
                self._db.execute("ROLLBACK")
                raise

        return self._retry_sqlite_busy(_mutate)

    def release(
        self,
        runner_id: str,
        principal_id: str | None = None,
        fencing_token: int | None = None,
    ) -> bool:
        """Release a lease; returns True if a row was removed.

        If *fencing_token* is given, raises :class:`StaleFencingTokenError`
        unless it matches the lease's current token -- an optimistic-
        concurrency guard against releasing a generation of the lease the
        caller no longer holds a current view of.
        """

        def _mutate() -> bool:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT principal_id, fencing_token FROM leases WHERE runner_id = ?", (runner_id,)
                ).fetchone()
                if row is None:
                    self._db.execute("ROLLBACK")
                    return False
                current_principal, current_fencing_token = row
                if fencing_token is not None and fencing_token != current_fencing_token:
                    self._db.execute("ROLLBACK")
                    raise StaleFencingTokenError(
                        f"runner {runner_id}: fencing token {fencing_token} is stale "
                        f"(current is {current_fencing_token})"
                    )
                if principal_id is not None and principal_id != current_principal:
                    self._db.execute("ROLLBACK")
                    return False
                self._db.execute("DELETE FROM leases WHERE runner_id = ?", (runner_id,))
                self._db.execute("COMMIT")
                return True
            except StaleFencingTokenError:
                raise
            except Exception:
                self._db.execute("ROLLBACK")
                raise

        released = self._retry_sqlite_busy(_mutate)
        if released:
            log.info("sqlite lease RELEASED runner=%s", runner_id)
        return released

    def prune_expired(self) -> int:
        """Delete all rows whose TTL has elapsed. Returns the count removed."""
        now = time.time()
        cur = self._execute("DELETE FROM leases WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,))
        removed: int = cur.rowcount
        if removed:
            log.debug("sqlite lease store: pruned %d expired lease(s)", removed)
        return removed

    def journal_mode(self) -> str:
        """The connection's SQLite journal mode, lower-cased (``"wal"`` when healthy)."""
        return str(self._execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._db.close()
        except Exception as e:  # noqa: BLE001
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
