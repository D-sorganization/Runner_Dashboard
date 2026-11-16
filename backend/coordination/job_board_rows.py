"""Durable job-board fences and lease TTL rows (#4270 B-09).

These rows used to live in two process dicts, so a dashboard restart stranded every
holder: its fence stopped matching and its lease was never swept. They now live in the
claim-state SQLite database, next to the compare-and-set claim rows.

``JobBoardRows`` is mixed into ``coordination.claim_state.ClaimStateStore``, which owns
the connection, the lock and the busy-retry policy.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

_T = TypeVar("_T")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS job_fences ("
    "  repo TEXT NOT NULL,"
    "  issue INTEGER NOT NULL,"
    "  fence INTEGER NOT NULL,"
    "  PRIMARY KEY (repo, issue)"
    ")",
    "CREATE TABLE IF NOT EXISTS job_leases ("
    "  repo_key TEXT NOT NULL,"
    "  issue INTEGER NOT NULL,"
    "  repo TEXT NOT NULL,"
    "  agent TEXT NOT NULL,"
    "  session TEXT NOT NULL,"
    "  fence INTEGER NOT NULL,"
    "  expires_at REAL NOT NULL,"
    "  ttl_hours REAL NOT NULL,"
    "  release_failures INTEGER NOT NULL DEFAULT 0,"
    "  PRIMARY KEY (repo_key, issue)"
    ")",
)
_LEASE_COLUMNS = "repo, issue, agent, session, fence, expires_at, ttl_hours, release_failures"
# A swept row is only touched while it is still the same lease: a claim or a renewal
# replaces the session, the fence or the expiry.
_SAME_LEASE = "repo_key = ? AND issue = ? AND session = ? AND fence = ? AND expires_at = ?"


@dataclass(frozen=True)
class JobLeaseRow:
    """One heartbeated job-board lease; ``expires_at`` is a UTC epoch."""

    repo: str
    issue: int
    agent: str
    session: str
    fence: int
    expires_at: float
    ttl_hours: float
    release_failures: int = 0


class JobBoardRows:
    """Fence and lease-row operations; the host class supplies the connection."""

    _db: sqlite3.Connection
    _lock: threading.RLock
    _execute: Callable[..., sqlite3.Cursor]
    _retry: Callable[[Callable[[], Any]], Any]

    def _create_job_board_tables(self) -> None:
        for statement in _SCHEMA:
            self._execute(statement)

    def _in_transaction(self, work: Callable[[], _T]) -> _T:
        """Run ``work`` in one ``BEGIN IMMEDIATE`` transaction, so other workers wait their turn."""

        def _mutate() -> _T:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                result = work()
                self._db.execute("COMMIT")
            except Exception:
                # A failed COMMIT leaves the transaction open; without the rollback every
                # later BEGIN on this connection would fail.
                self._db.execute("ROLLBACK")
                raise
            return result

        with self._lock:
            return self._retry(_mutate)

    @staticmethod
    def _job_key(repo: str, issue: int) -> tuple[str, int]:
        return repo.casefold(), int(issue)

    def _same_lease(self, row: JobLeaseRow) -> tuple[Any, ...]:
        return (*self._job_key(row.repo, row.issue), row.session, row.fence, row.expires_at)

    def mint_fence(self, repo: str, issue: int) -> int:
        """The next fence for ``repo#issue``: one more than any fence ever minted for it."""
        key = self._job_key(repo, issue)

        def _work() -> int:
            self._db.execute(
                "INSERT INTO job_fences (repo, issue, fence) VALUES (?, ?, 1) "
                "ON CONFLICT (repo, issue) DO UPDATE SET fence = fence + 1",
                key,
            )
            row = self._db.execute("SELECT fence FROM job_fences WHERE repo = ? AND issue = ?", key).fetchone()
            return int(row["fence"])

        return self._in_transaction(_work)

    def current_fence(self, repo: str, issue: int) -> int:
        """The last fence minted for ``repo#issue``, or 0 when none was."""
        with self._lock:
            row = self._execute(
                "SELECT fence FROM job_fences WHERE repo = ? AND issue = ?", self._job_key(repo, issue)
            ).fetchone()
        return int(row["fence"]) if row is not None else 0

    def put_job_lease(self, row: JobLeaseRow) -> None:
        """Store ``row`` as the lease of its job, with a full release-retry budget."""
        values = (
            *self._job_key(row.repo, row.issue),
            row.repo,
            row.agent,
            row.session,
            int(row.fence),
            float(row.expires_at),
            float(row.ttl_hours),
        )
        with self._lock:
            self._execute(
                "INSERT OR REPLACE INTO job_leases "
                "(repo_key, issue, repo, agent, session, fence, expires_at, ttl_hours, release_failures) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                values,
            )

    def get_job_lease(self, repo: str, issue: int) -> JobLeaseRow | None:
        with self._lock:
            row = self._execute(
                f"SELECT {_LEASE_COLUMNS} FROM job_leases WHERE repo_key = ? AND issue = ?",  # noqa: S608
                self._job_key(repo, issue),
            ).fetchone()
        return self._lease_from(row) if row is not None else None

    def list_job_leases(self) -> list[JobLeaseRow]:
        with self._lock:
            rows = self._execute(
                f"SELECT {_LEASE_COLUMNS} FROM job_leases ORDER BY repo_key, issue"  # noqa: S608
            ).fetchall()
        return [self._lease_from(row) for row in rows]

    def drop_job_lease(self, repo: str, issue: int) -> None:
        with self._lock:
            self._execute("DELETE FROM job_leases WHERE repo_key = ? AND issue = ?", self._job_key(repo, issue))

    def forget_job_lease(self, row: JobLeaseRow) -> bool:
        """Delete ``row`` unless a newer claim or renewal replaced it; True when it was deleted."""
        with self._lock:
            cursor = self._execute(f"DELETE FROM job_leases WHERE {_SAME_LEASE}", self._same_lease(row))  # noqa: S608
        return cursor.rowcount > 0

    def count_job_release_failure(self, row: JobLeaseRow) -> int:
        """Record one failed release of ``row``; 0 when the row was replaced and the failure is moot."""
        same = self._same_lease(row)

        def _work() -> int:
            changed = self._db.execute(
                f"UPDATE job_leases SET release_failures = release_failures + 1 WHERE {_SAME_LEASE}",  # noqa: S608
                same,
            ).rowcount
            if not changed:
                return 0
            found = self._db.execute(
                f"SELECT release_failures FROM job_leases WHERE {_SAME_LEASE}",  # noqa: S608
                same,
            ).fetchone()
            return int(found["release_failures"])

        return self._in_transaction(_work)

    def purge_job_board_rows(self) -> None:
        """Delete every fence and lease row (tests)."""
        with self._lock:
            self._execute("DELETE FROM job_fences")
            self._execute("DELETE FROM job_leases")

    @staticmethod
    def _lease_from(row: sqlite3.Row) -> JobLeaseRow:
        return JobLeaseRow(
            repo=str(row["repo"]),
            issue=int(row["issue"]),
            agent=str(row["agent"]),
            session=str(row["session"]),
            fence=int(row["fence"]),
            expires_at=float(row["expires_at"]),
            ttl_hours=float(row["ttl_hours"]),
            release_failures=int(row["release_failures"]),
        )
