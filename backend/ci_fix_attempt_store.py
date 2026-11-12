"""Atomic CI-fix attempt counters shared with the hub lock store (#4273)."""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from time_utils import utc_now_iso

DEFAULT_PER_HEAD_CAP = int(os.environ.get("CI_FIX_ATTEMPT_CAP_PER_HEAD", "2"))
DEFAULT_PER_PR_CAP = int(os.environ.get("CI_FIX_ATTEMPT_CAP_PER_PR", "4"))


@dataclass(frozen=True, slots=True)
class AttemptCapDecision:
    """Whether a new CI-fix dispatch is allowed under per-head and per-PR caps."""

    allowed: bool
    reason: str = ""
    head_attempts: int = 0
    total_attempts: int = 0
    reservation_id: int | None = None


class CIFixAttemptStore:
    """SQLite-backed attempt rows in the same DB file as CI-fix locks (hub-visible)."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self._path), isolation_level=None, check_same_thread=False, timeout=30.0)
            db.execute("PRAGMA busy_timeout=30000")
            db.execute(
                "CREATE TABLE IF NOT EXISTS ci_fix_attempts ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "repo TEXT NOT NULL, pr_number INTEGER NOT NULL, head_sha TEXT NOT NULL, "
                "recorded_at TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_ci_fix_attempts_repo_pr ON ci_fix_attempts(repo, pr_number)")
            self._db = db
        return self._db

    @staticmethod
    def _key(repo: str, pr_number: int) -> tuple[str, int]:
        return (repo.strip().lower(), pr_number)

    def counts(self, repo: str, pr_number: int, head_sha: str) -> tuple[int, int]:
        repo_key, pr = self._key(repo, pr_number)
        head = (head_sha or "").strip().lower()
        with self._lock:
            db = self._conn()
            total_row = db.execute(
                "SELECT COUNT(*) FROM ci_fix_attempts WHERE repo = ? AND pr_number = ?",
                (repo_key, pr),
            ).fetchone()
            head_row = db.execute(
                "SELECT COUNT(*) FROM ci_fix_attempts WHERE repo = ? AND pr_number = ? AND head_sha = ?",
                (repo_key, pr, head),
            ).fetchone()
        total = int(total_row[0] if total_row else 0)
        head_count = int(head_row[0] if head_row else 0)
        return head_count, total

    def check_caps(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        *,
        per_head_cap: int = DEFAULT_PER_HEAD_CAP,
        per_pr_cap: int = DEFAULT_PER_PR_CAP,
    ) -> AttemptCapDecision:
        head_count, total = self.counts(repo, pr_number, head_sha)
        if head_count >= per_head_cap:
            return AttemptCapDecision(
                allowed=False,
                reason=(
                    f"per-head CI-fix attempt cap reached ({head_count}/{per_head_cap}) "
                    f"for {repo}#{pr_number} at {head_sha[:7]}"
                ),
                head_attempts=head_count,
                total_attempts=total,
            )
        if total >= per_pr_cap:
            return AttemptCapDecision(
                allowed=False,
                reason=f"per-PR CI-fix attempt cap reached ({total}/{per_pr_cap}) for {repo}#{pr_number}",
                head_attempts=head_count,
                total_attempts=total,
            )
        return AttemptCapDecision(allowed=True, head_attempts=head_count, total_attempts=total)

    def reserve_attempt(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        *,
        per_head_cap: int = DEFAULT_PER_HEAD_CAP,
        per_pr_cap: int = DEFAULT_PER_PR_CAP,
    ) -> AttemptCapDecision:
        """Check caps and record one attempt atomically when allowed."""
        repo_key, pr = self._key(repo, pr_number)
        head = (head_sha or "").strip().lower()
        with self._lock:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                total_row = db.execute(
                    "SELECT COUNT(*) FROM ci_fix_attempts WHERE repo = ? AND pr_number = ?",
                    (repo_key, pr),
                ).fetchone()
                head_row = db.execute(
                    "SELECT COUNT(*) FROM ci_fix_attempts WHERE repo = ? AND pr_number = ? AND head_sha = ?",
                    (repo_key, pr, head),
                ).fetchone()
                head_count = int(head_row[0] if head_row else 0)
                total = int(total_row[0] if total_row else 0)
                if head_count >= per_head_cap:
                    db.execute("ROLLBACK")
                    return AttemptCapDecision(
                        allowed=False,
                        reason=(
                            f"per-head CI-fix attempt cap reached ({head_count}/{per_head_cap}) for {repo}#{pr_number}"
                        ),
                        head_attempts=head_count,
                        total_attempts=total,
                    )
                if total >= per_pr_cap:
                    db.execute("ROLLBACK")
                    return AttemptCapDecision(
                        allowed=False,
                        reason=f"per-PR CI-fix attempt cap reached ({total}/{per_pr_cap}) for {repo}#{pr_number}",
                        head_attempts=head_count,
                        total_attempts=total,
                    )
                cur = db.execute(
                    "INSERT INTO ci_fix_attempts (repo, pr_number, head_sha, recorded_at) VALUES (?, ?, ?, ?)",
                    (repo_key, pr, head, utc_now_iso()),
                )
                reservation_id = cur.lastrowid
                if reservation_id is None:
                    db.execute("ROLLBACK")
                    raise RuntimeError("ci_fix_attempts INSERT did not return row id")
                db.execute("COMMIT")
                return AttemptCapDecision(
                    allowed=True,
                    head_attempts=head_count + 1,
                    total_attempts=total + 1,
                    reservation_id=reservation_id,
                )
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def release_reserved_attempt(self, reservation_id: int) -> bool:
        """Drop the attempt row created by reserve_attempt when dispatch never launched (#4273)."""
        if reservation_id <= 0:
            return False
        with self._lock:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT id FROM ci_fix_attempts WHERE id = ?",
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    db.execute("ROLLBACK")
                    return False
                db.execute("DELETE FROM ci_fix_attempts WHERE id = ?", (reservation_id,))
                db.execute("COMMIT")
                return True
            except BaseException:
                db.execute("ROLLBACK")
                raise


def get_ci_fix_attempt_store(db_path: Path | None = None) -> CIFixAttemptStore:
    if db_path is None:
        from ci_fix_locks import DEFAULT_LOCK_DB_PATH

        db_path = DEFAULT_LOCK_DB_PATH
    return CIFixAttemptStore(db_path)


_GLOBAL_ATTEMPT_STORE: CIFixAttemptStore | None = None


def global_ci_fix_attempt_store() -> CIFixAttemptStore:
    global _GLOBAL_ATTEMPT_STORE
    if _GLOBAL_ATTEMPT_STORE is None:
        _GLOBAL_ATTEMPT_STORE = get_ci_fix_attempt_store()
    return _GLOBAL_ATTEMPT_STORE
