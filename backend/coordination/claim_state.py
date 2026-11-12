"""Durable issue-claim state machine and compare-and-set acquire (#4270 PR 2).

Hub-local SQLite is the coordination anti-double-win layer before RM lease posts.
GitHub (via ``post_agent_lease``) stays authoritative for operators; this store
prevents two concurrent acquires in the same process or across workers sharing
the hub database from both posting.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.coordination.claim_state")

_SQLITE_BUSY_RETRIES = 8
_SQLITE_BUSY_SLEEP_S = 0.05
_DEFAULT_CAS_RESERVE_TTL_S = float(os.environ.get("CLAIM_STATE_CAS_TTL_S", "300"))


def cas_reserve_ttl_seconds() -> float:
    raw = os.environ.get("CLAIM_STATE_CAS_TTL_S")
    if raw is not None and raw != "":
        return float(raw)
    return _DEFAULT_CAS_RESERVE_TTL_S


_store_lock = threading.Lock()
_store: ClaimStateStore | None = None


class ClaimState(StrEnum):
    UNHELD = "unheld"
    HELD = "held"


LEGAL_TRANSITIONS: dict[ClaimState, frozenset[ClaimState]] = {
    ClaimState.UNHELD: frozenset({ClaimState.HELD}),
    ClaimState.HELD: frozenset({ClaimState.UNHELD, ClaimState.HELD}),
}


def is_legal_transition(
    current: ClaimState,
    target: ClaimState,
    *,
    same_holder: bool = False,
) -> bool:
    if current == target and current == ClaimState.HELD:
        return same_holder
    if current == target:
        return False
    allowed = LEGAL_TRANSITIONS.get(current, frozenset())
    return target in allowed


@dataclass(frozen=True)
class ClaimRow:
    repo: str
    issue: int
    agent: str
    session: str
    state: ClaimState
    expires_at: float | None = None


@dataclass(frozen=True)
class AcquireResult:
    acquired: bool
    holder_agent: str = ""
    holder_session: str = ""
    holder_expires_at: float | None = None


def _default_db_path() -> Path:
    base = Path(os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR", str(Path("~/.config/runner-dashboard").expanduser())))
    return base / "claim_state.sqlite3"


class ClaimStateStore:
    """SQLite WAL store: one row per ``repo#issue`` with CAS acquire."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=30.0)
        self._db.row_factory = sqlite3.Row
        self._execute("PRAGMA busy_timeout=30000")
        self._execute("PRAGMA journal_mode=WAL")
        self._execute(
            "CREATE TABLE IF NOT EXISTS issue_claims ("
            "  repo TEXT NOT NULL,"
            "  issue INTEGER NOT NULL,"
            "  agent TEXT NOT NULL,"
            "  session TEXT NOT NULL,"
            "  state TEXT NOT NULL,"
            "  updated_at REAL NOT NULL,"
            "  expires_at REAL,"
            "  PRIMARY KEY (repo, issue)"
            ")"
        )
        columns = {str(row[1]) for row in self._execute("PRAGMA table_info(issue_claims)").fetchall()}
        if "expires_at" not in columns:
            self._execute("ALTER TABLE issue_claims ADD COLUMN expires_at REAL")

    def _retry(self, operation: Any) -> Any:
        for attempt in range(_SQLITE_BUSY_RETRIES):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == _SQLITE_BUSY_RETRIES - 1:
                    raise
                time.sleep(_SQLITE_BUSY_SLEEP_S * (2**attempt))
        raise AssertionError("unreachable")

    def _execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self._retry(lambda: self._db.execute(sql, parameters))

    @staticmethod
    def _key(repo: str, issue: int) -> tuple[str, int]:
        return repo.casefold(), int(issue)

    def read(self, repo: str, issue: int) -> ClaimRow | None:
        repo_key, issue_num = self._key(repo, issue)
        with self._lock:
            row = self._execute(
                "SELECT repo, issue, agent, session, state, expires_at, updated_at "
                "FROM issue_claims WHERE repo = ? AND issue = ?",
                (repo_key, issue_num),
            ).fetchone()
        if row is None or str(row["state"]) == ClaimState.UNHELD:
            return None
        expires_raw = row["expires_at"]
        expires_at = float(expires_raw) if expires_raw is not None else None
        if expires_at is None:
            expires_at = float(row["updated_at"]) + cas_reserve_ttl_seconds()
        return ClaimRow(
            repo=str(row["repo"]),
            issue=int(row["issue"]),
            agent=str(row["agent"]),
            session=str(row["session"]),
            state=ClaimState(str(row["state"])),
            expires_at=expires_at,
        )

    @staticmethod
    def _row_expires_at(row: sqlite3.Row, *, now: float) -> float:
        raw = row["expires_at"]
        if raw is not None:
            return float(raw)
        return float(row["updated_at"]) + cas_reserve_ttl_seconds()

    def try_acquire(
        self,
        repo: str,
        issue: int,
        *,
        agent: str,
        session: str,
        reserve_ttl_s: float | None = None,
    ) -> AcquireResult:
        repo_key, issue_num = self._key(repo, issue)
        now = time.time()
        ttl = cas_reserve_ttl_seconds() if reserve_ttl_s is None else float(reserve_ttl_s)
        expires = now + ttl

        with self._lock:

            def _mutate() -> AcquireResult:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    row = self._db.execute(
                        "SELECT agent, session, state, expires_at, updated_at FROM issue_claims "
                        "WHERE repo = ? AND issue = ?",
                        (repo_key, issue_num),
                    ).fetchone()
                    if row is None:
                        if not is_legal_transition(ClaimState.UNHELD, ClaimState.HELD):
                            raise AssertionError("unheld->held must be legal")
                        self._db.execute(
                            "INSERT INTO issue_claims "
                            "(repo, issue, agent, session, state, updated_at, expires_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (repo_key, issue_num, agent, session, ClaimState.HELD, now, expires),
                        )
                        self._db.execute("COMMIT")
                        return AcquireResult(acquired=True)
                    state = ClaimState(str(row["state"]))
                    holder_agent, holder_session = str(row["agent"]), str(row["session"])
                    if state == ClaimState.UNHELD:
                        self._db.execute(
                            "UPDATE issue_claims SET agent = ?, session = ?, state = ?, updated_at = ?, expires_at = ? "
                            "WHERE repo = ? AND issue = ?",
                            (agent, session, ClaimState.HELD, now, expires, repo_key, issue_num),
                        )
                        self._db.execute("COMMIT")
                        return AcquireResult(acquired=True)
                    if holder_agent == agent and holder_session == session:
                        if not is_legal_transition(ClaimState.HELD, ClaimState.HELD, same_holder=True):
                            raise AssertionError("renewal must be legal")
                        self._db.execute(
                            "UPDATE issue_claims SET updated_at = ?, expires_at = ? WHERE repo = ? AND issue = ?",
                            (now, expires, repo_key, issue_num),
                        )
                        self._db.execute("COMMIT")
                        return AcquireResult(acquired=True)
                    holder_expires = self._row_expires_at(row, now=now)
                    if now < holder_expires:
                        self._db.execute("ROLLBACK")
                        return AcquireResult(
                            acquired=False,
                            holder_agent=holder_agent,
                            holder_session=holder_session,
                            holder_expires_at=holder_expires,
                        )
                    cur = self._db.execute(
                        "UPDATE issue_claims SET agent = ?, session = ?, state = ?, updated_at = ?, expires_at = ? "
                        "WHERE repo = ? AND issue = ? AND agent = ? AND session = ? AND state = ?",
                        (
                            agent,
                            session,
                            ClaimState.HELD,
                            now,
                            expires,
                            repo_key,
                            issue_num,
                            holder_agent,
                            holder_session,
                            ClaimState.HELD,
                        ),
                    )
                    if cur.rowcount != 1:
                        self._db.execute("ROLLBACK")
                        fresh = self._db.execute(
                            "SELECT agent, session, expires_at, updated_at FROM issue_claims "
                            "WHERE repo = ? AND issue = ?",
                            (repo_key, issue_num),
                        ).fetchone()
                        if fresh is None:
                            return AcquireResult(acquired=False)
                        return AcquireResult(
                            acquired=False,
                            holder_agent=str(fresh["agent"]),
                            holder_session=str(fresh["session"]),
                            holder_expires_at=self._row_expires_at(fresh, now=now),
                        )
                    self._db.execute("COMMIT")
                    return AcquireResult(acquired=True)
                except Exception:
                    self._db.execute("ROLLBACK")
                    raise

            return self._retry(_mutate)

    def release(self, repo: str, issue: int, *, agent: str, session: str) -> bool:
        repo_key, issue_num = self._key(repo, issue)
        now = time.time()

        with self._lock:

            def _mutate() -> bool:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    row = self._db.execute(
                        "SELECT agent, session, state FROM issue_claims WHERE repo = ? AND issue = ?",
                        (repo_key, issue_num),
                    ).fetchone()
                    if row is None:
                        self._db.execute("ROLLBACK")
                        return False
                    if str(row["agent"]) != agent or str(row["session"]) != session:
                        self._db.execute("ROLLBACK")
                        return False
                    self._db.execute(
                        "UPDATE issue_claims SET state = ?, updated_at = ? WHERE repo = ? AND issue = ?",
                        (ClaimState.UNHELD, now, repo_key, issue_num),
                    )
                    self._db.execute("COMMIT")
                    return True
                except Exception:
                    self._db.execute("ROLLBACK")
                    raise

            return self._retry(_mutate)

    def abandon_acquire(self, repo: str, issue: int, *, agent: str, session: str) -> None:
        """Drop a CAS reservation when the subsequent RM lease post failed."""
        self.release(repo, issue, agent=agent, session=session)

    def close(self) -> None:
        self._db.close()


def get_store() -> ClaimStateStore:
    global _store
    with _store_lock:
        if _store is None:
            override = os.environ.get("CLAIM_STATE_DB")
            path = Path(override).expanduser() if override else _default_db_path()
            _store = ClaimStateStore(path)
        return _store


def reset_claim_state_store() -> None:
    """Forget the singleton and close any open connection (tests)."""
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
        _store = None


def acquire_compare_and_set(repo: str, issue: int, *, agent: str, session: str) -> AcquireResult:
    return get_store().try_acquire(repo, issue, agent=agent, session=session)


def holder_status_from_row(repo: str, issue: int, result: AcquireResult) -> dict[str, Any]:
    """Shape a ``ClaimHeldError``-compatible status dict from a failed CAS."""
    expires_iso: str | None = None
    if result.holder_expires_at is not None:
        expires_iso = dt.datetime.fromtimestamp(result.holder_expires_at, dt.UTC).isoformat()
    return {
        "available": True,
        "held": True,
        "agent": result.holder_agent,
        "session": result.holder_session,
        "reason": f"durable claim held by {result.holder_agent or 'another agent'}",
        "expires_at": expires_iso,
    }
