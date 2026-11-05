"""SQLite WAL-backed per-agent inbox with must-acknowledge messages (#3931 S3, refs #3878).

Clean-room: built from public docs/behavior descriptions only. Mirrors
``path_lease_store.py``'s WAL pattern (busy-retry, ``BEGIN IMMEDIATE``).
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

_SQLITE_BUSY_RETRIES = 8
_SQLITE_BUSY_SLEEP_S = 0.05
_T = TypeVar("_T")
_COLUMNS = "message_id, to_agent, from_agent, body, sent_at, requires_ack, ack_deadline_seconds, acked_at"


def default_agent_inbox_db_path() -> Path:
    override = os.environ.get("DASHBOARD_AGENT_INBOX_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("DASHBOARD_STATE_DIR", "state")) / "agent_inbox.sqlite3"


class InboxConflictError(RuntimeError):
    """The message doesn't exist, or isn't addressed to the acknowledging agent."""


@dataclass(frozen=True)
class InboxMessageRecord:
    message_id: str
    to_agent: str
    from_agent: str
    body: str
    sent_at: float
    requires_ack: bool
    ack_deadline_seconds: float | None
    acked_at: float | None


class AgentInboxStore:
    """SQLite WAL-backed store for per-agent inbox messages."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=30.0)
        self._execute("PRAGMA busy_timeout=30000")
        self._execute("PRAGMA journal_mode=WAL")
        self._execute(
            "CREATE TABLE IF NOT EXISTS agent_inbox ("
            "  message_id TEXT PRIMARY KEY, to_agent TEXT NOT NULL, from_agent TEXT NOT NULL,"
            "  body TEXT NOT NULL, sent_at REAL NOT NULL, requires_ack INTEGER NOT NULL,"
            "  ack_deadline_seconds REAL, acked_at REAL"
            ")"
        )

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
    def _row_to_record(row: tuple[Any, ...]) -> InboxMessageRecord:
        message_id, to_agent, from_agent, body, sent_at, requires_ack, ack_deadline_seconds, acked_at = row
        return InboxMessageRecord(
            message_id=message_id,
            to_agent=to_agent,
            from_agent=from_agent,
            body=body,
            sent_at=sent_at,
            requires_ack=bool(requires_ack),
            ack_deadline_seconds=ack_deadline_seconds,
            acked_at=acked_at,
        )

    def send(
        self,
        to: str,
        from_: str,
        body: str,
        *,
        requires_ack: bool,
        ack_deadline_seconds: float | None,
        now: float | None = None,
    ) -> InboxMessageRecord:
        """Store a message addressed to *to*. ``now`` is an injectable clock for tests."""
        sent_at = now if now is not None else time.time()
        message_id = str(uuid.uuid4())

        def _body() -> InboxMessageRecord:
            self._db.execute(
                "INSERT INTO agent_inbox "
                "(message_id, to_agent, from_agent, body, sent_at, requires_ack, ack_deadline_seconds, acked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (message_id, to, from_, body, sent_at, int(requires_ack), ack_deadline_seconds),
            )
            return InboxMessageRecord(message_id, to, from_, body, sent_at, requires_ack, ack_deadline_seconds, None)

        return self._in_transaction(_body)

    def ack(self, message_id: str, agent: str) -> InboxMessageRecord:
        """Mark a message acknowledged by its addressee.

        Raises :class:`InboxConflictError` if the message doesn't exist or
        isn't addressed to *agent*. Idempotent: acking an already-acked
        message simply refreshes ``acked_at``.
        """

        def _body() -> InboxMessageRecord:
            row = self._db.execute(f"SELECT {_COLUMNS} FROM agent_inbox WHERE message_id = ?", (message_id,)).fetchone()
            if row is None:
                raise InboxConflictError(f"no message with id {message_id!r}")
            rec = self._row_to_record(row)
            if rec.to_agent != agent:
                raise InboxConflictError(f"message {message_id!r} is not addressed to {agent!r}")
            acked_at = time.time()
            self._db.execute("UPDATE agent_inbox SET acked_at = ? WHERE message_id = ?", (acked_at, message_id))
            return InboxMessageRecord(
                rec.message_id,
                rec.to_agent,
                rec.from_agent,
                rec.body,
                rec.sent_at,
                rec.requires_ack,
                rec.ack_deadline_seconds,
                acked_at,
            )

        return self._in_transaction(_body)

    def list_for(self, agent: str, *, include_acked: bool = False) -> list[InboxMessageRecord]:
        """Messages addressed to *agent*, newest-sent last."""
        if include_acked:
            rows = self._execute(
                f"SELECT {_COLUMNS} FROM agent_inbox WHERE to_agent = ? ORDER BY sent_at", (agent,)
            ).fetchall()
        else:
            rows = self._execute(
                f"SELECT {_COLUMNS} FROM agent_inbox WHERE to_agent = ? AND acked_at IS NULL ORDER BY sent_at",
                (agent,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def overdue(self, *, now: float | None = None) -> list[InboxMessageRecord]:
        """Ack-required, unacked messages whose deadline has passed as of *now*."""
        current = now if now is not None else time.time()
        rows = self._execute(
            f"SELECT {_COLUMNS} FROM agent_inbox WHERE requires_ack = 1 AND acked_at IS NULL "
            "AND ack_deadline_seconds IS NOT NULL AND sent_at + ack_deadline_seconds <= ? ORDER BY sent_at",
            (current,),
        ).fetchall()
        return [self._row_to_record(row) for row in rows]


def alert_overdue_acks(
    overdue_messages: Sequence[InboxMessageRecord], *, send: Callable[[InboxMessageRecord], None]
) -> int:
    """Raise one alert per overdue ack-required message through an injected ``send`` callable.

    Pure/DI (#3931 S3) so the real Barb-alert delivery mechanism (``barb_cli.py``/
    ``staff/barb_outbox.py``) stays swappable and unit-testable without live calls;
    wiring a periodic trigger with a concrete ``send`` is a follow-up.
    """
    for message in overdue_messages:
        send(message)
    return len(overdue_messages)
