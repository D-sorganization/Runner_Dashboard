"""Durable issue-claim state machine and compare-and-set acquire (#4270 PR 2).

Hub-local SQLite is the coordination anti-double-win layer before RM lease posts.
GitHub (via ``post_agent_lease``) stays authoritative for operators; this store
prevents two concurrent acquires in the same process or across workers sharing
the hub database from both posting.
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class AcquireResult:
    acquired: bool
    holder_agent: str = ""
    holder_session: str = ""


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
            "  PRIMARY KEY (repo, issue)"
            ")"
        )

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
                "SELECT repo, issue, agent, session, state FROM issue_claims WHERE repo = ? AND issue = ?",
                (repo_key, issue_num),
            ).fetchone()
        if row is None or str(row["state"]) == ClaimState.UNHELD:
            return None
        return ClaimRow(
            repo=str(row["repo"]),
            issue=int(row["issue"]),
            agent=str(row["agent"]),
            session=str(row["session"]),
            state=ClaimState(str(row["state"])),
        )

    def try_acquire(self, repo: str, issue: int, *, agent: str, session: str) -> AcquireResult:
        repo_key, issue_num = self._key(repo, issue)
        now = time.time()

        with self._lock:

            def _mutate() -> AcquireResult:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    row = self._db.execute(
                        "SELECT agent, session, state FROM issue_claims WHERE repo = ? AND issue = ?",
                        (repo_key, issue_num),
                    ).fetchone()
                    if row is None:
                        if not is_legal_transition(ClaimState.UNHELD, ClaimState.HELD):
                            raise AssertionError("unheld->held must be legal")
                        self._db.execute(
                            "INSERT INTO issue_claims (repo, issue, agent, session, state, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (repo_key, issue_num, agent, session, ClaimState.HELD, now),
                        )
                        self._db.execute("COMMIT")
                        return AcquireResult(acquired=True)
                    state = ClaimState(str(row["state"]))
                    holder_agent, holder_session = str(row["agent"]), str(row["session"])
                    if state == ClaimState.UNHELD:
                        self._db.execute(
                            "UPDATE issue_claims SET agent = ?, session = ?, state = ?, updated_at = ? "
                            "WHERE repo = ? AND issue = ?",
                            (agent, session, ClaimState.HELD, now, repo_key, issue_num),
                        )
                        self._db.execute("COMMIT")
                        return AcquireResult(acquired=True)
                    if holder_agent == agent and holder_session == session:
                        if not is_legal_transition(ClaimState.HELD, ClaimState.HELD, same_holder=True):
                            raise AssertionError("renewal must be legal")
                        self._db.execute(
                            "UPDATE issue_claims SET updated_at = ? WHERE repo = ? AND issue = ?",
                            (now, repo_key, issue_num),
                        )
                        self._db.execute("COMMIT")
                        return AcquireResult(acquired=True)
                    self._db.execute("ROLLBACK")
                    return AcquireResult(acquired=False, holder_agent=holder_agent, holder_session=holder_session)
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
    return {
        "available": True,
        "held": True,
        "agent": result.holder_agent,
        "session": result.holder_session,
        "reason": f"durable claim held by {result.holder_agent or 'another agent'}",
        "expires_at": None,
    }
