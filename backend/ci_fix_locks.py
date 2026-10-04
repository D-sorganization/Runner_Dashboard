"""Per-PR concurrency locks for RD-1 CI-fix sessions (#1846), with expiry (#1881).

Locks live in one SQLite table so every dashboard worker process (``WORKERS > 1``) sees the
same lock: :meth:`CIFixLockManager.acquire` checks and inserts inside ``BEGIN IMMEDIATE``,
which SQLite serialises across processes. ``path=None`` keeps a private in-memory store
(tests, single process).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from time_utils import utc_now_iso

__all__ = ["DEFAULT_LOCK_DB_PATH", "DEFAULT_LOCK_TTL_SECONDS", "GLOBAL_CI_FIX_LOCK_MGR", "CIFixLockManager"]

# #1881: a lock outlives neither its staff run nor this TTL (default 2 h, the lease length).
DEFAULT_LOCK_TTL_SECONDS = float(os.environ.get("CI_FIX_LOCK_TTL_SECONDS", "7200"))
DEFAULT_LOCK_DB_PATH = Path(
    os.environ.get(
        "CI_FIX_LOCK_DB",
        str(
            Path(os.environ.get("RUNNER_DASHBOARD_DATA_DIR", Path.home() / ".local" / "share" / "runner-dashboard"))
            / "ci_fix_locks.db"
        ),
    )
)


class CIFixLockManager:
    """Per-PR concurrency lock for CI-fix sessions, shared by every process on one store.

    A lock is dropped when its TTL elapses or, once a staff run is attached, as soon as
    ``run_is_live(run_id)`` reports the run finished. A liveness probe that raises keeps
    the lock (the TTL still bounds it).
    """

    def __init__(
        self,
        ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.time,
        run_is_live: Callable[[str], bool] | None = None,
        path: Path | None = None,
    ) -> None:
        self._ttl = DEFAULT_LOCK_TTL_SECONDS if ttl_seconds is None else float(ttl_seconds)
        assert self._ttl > 0, "lock TTL must be positive"  # noqa: S101 - constructor contract
        self._clock = clock
        self._run_is_live = run_is_live
        self._path = path
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        """Open the store on first use (no file is created at import time)."""
        if self._db is None:
            target = ":memory:"
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                target = str(self._path)
            db = sqlite3.connect(target, isolation_level=None, check_same_thread=False, timeout=30.0)
            db.execute("PRAGMA busy_timeout=30000")
            db.execute(
                "CREATE TABLE IF NOT EXISTS ci_fix_locks (repo TEXT NOT NULL, pr_number INTEGER NOT NULL, "
                "repo_display TEXT NOT NULL, session_id TEXT NOT NULL, run_id TEXT NOT NULL, "
                "acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL, deadline REAL NOT NULL, "
                "PRIMARY KEY (repo, pr_number))"
            )
            self._db = db
        return self._db

    @staticmethod
    def _key(repo: str, pr_number: int) -> tuple[str, int]:
        return (repo.strip().lower(), pr_number)

    def _ended(self, deadline: float, run_id: str, now: float) -> bool:
        if now >= deadline:
            return True
        if not run_id or self._run_is_live is None:
            return False
        try:
            return not self._run_is_live(run_id)
        except Exception:  # noqa: BLE001 - an unreadable run store keeps the lock until its TTL
            return False

    def _live_rows(self, where: str = "", params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Rows still held; ended rows are deleted on the way (caller holds ``self._lock``)."""
        db = self._conn()
        sql = (
            "SELECT repo, pr_number, repo_display, session_id, run_id, acquired_at, expires_at, deadline "
            "FROM ci_fix_locks"
        )
        rows = db.execute(f"{sql} {where}", params).fetchall()  # noqa: S608 - fixed clauses only
        now, live = self._clock(), []
        for repo, pr, display, session_id, run_id, acquired_at, expires_at, deadline in rows:
            if self._ended(deadline, run_id, now):
                db.execute(
                    "DELETE FROM ci_fix_locks WHERE repo = ? AND pr_number = ? AND deadline = ?", (repo, pr, deadline)
                )
                continue
            live.append(
                {
                    "repo": display,
                    "pr_number": pr,
                    "session_id": session_id,
                    "run_id": run_id,
                    "acquired_at": acquired_at,
                    "expires_at": expires_at,
                }
            )
        return live

    def acquire(self, repo: str, pr_number: int, session_id: str = "") -> bool:
        """Acquire the single-session lock for a PR; atomic across processes sharing the store."""
        key = self._key(repo, pr_number)
        with self._lock:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                if self._live_rows("WHERE repo = ? AND pr_number = ?", key):
                    db.execute("ROLLBACK")
                    return False
                deadline = self._clock() + self._ttl
                db.execute(
                    "INSERT OR FAIL INTO ci_fix_locks VALUES (?, ?, ?, ?, '', ?, ?, ?)",
                    (
                        *key,
                        repo,
                        session_id,
                        utc_now_iso(),
                        datetime.fromtimestamp(deadline, UTC).isoformat(),
                        deadline,
                    ),
                )
                db.execute("COMMIT")
                return True
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def attach_run(self, repo: str, pr_number: int, run_id: str) -> bool:
        """Bind a held lock to the staff run it launched, so the lock ends with the run."""
        with self._lock:
            cur = self._conn().execute(
                "UPDATE ci_fix_locks SET run_id = ? WHERE repo = ? AND pr_number = ?",
                (run_id, *self._key(repo, pr_number)),
            )
            return cur.rowcount > 0

    def release(self, repo: str, pr_number: int) -> bool:
        """Release concurrency lock for a PR."""
        with self._lock:
            cur = self._conn().execute(
                "DELETE FROM ci_fix_locks WHERE repo = ? AND pr_number = ?", self._key(repo, pr_number)
            )
            return cur.rowcount > 0

    def is_locked(self, repo: str, pr_number: int) -> bool:
        """Check if PR currently has an active CI-fix session."""
        return self.get_lock_info(repo, pr_number) is not None

    def get_lock_info(self, repo: str, pr_number: int) -> dict[str, Any] | None:
        """Return lock metadata if locked."""
        with self._lock:
            rows = self._live_rows("WHERE repo = ? AND pr_number = ?", self._key(repo, pr_number))
            return rows[0] if rows else None

    def list_locks(self) -> list[dict[str, Any]]:
        """Return snapshot of all active locks."""
        with self._lock:
            return self._live_rows()


def _staff_run_is_live(run_id: str) -> bool:
    """Whether a staff run is still queued or working (the default lock liveness probe)."""
    from staff.store import ACTIVE_STATUSES, get_store  # noqa: PLC0415

    rec = get_store().get_run(run_id)
    return rec is not None and rec.status in (*ACTIVE_STATUSES, "needs_input")


# Global instance shared by every worker through the on-disk store.
GLOBAL_CI_FIX_LOCK_MGR = CIFixLockManager(run_is_live=_staff_run_is_live, path=DEFAULT_LOCK_DB_PATH)
