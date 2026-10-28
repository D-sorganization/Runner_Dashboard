"""Versioned fleet activity metadata envelope and append-only SQLite log."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GAP_KINDS = ("OverflowGap", "RetentionGap", "CaptureGap")
MAX_PAYLOAD_BYTES = 16_384
_initialization_lock = threading.RLock()
ActivitySource = Literal[
    "staff_run",
    "coordination",
    "agent_bus",
    "recorder",
    "desktop_adapter",
    "checkin",
    "job_board",
    "github",
]


class ActivityEnvelope(BaseModel):
    """Canonical v1 metadata record. Extra fields fail closed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    event_id: str = Field(min_length=1, max_length=128)
    source: ActivitySource
    principal: str = Field(min_length=1, max_length=128)
    node: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    seq: int = Field(ge=0)
    ts: datetime
    repo: str | None = Field(default=None, max_length=128)
    issue: int | None = Field(default=None, gt=0)
    branch: str | None = Field(default=None, max_length=256)
    pr: int | None = Field(default=None, gt=0)
    kind: str = Field(min_length=1, max_length=64)
    redaction_version: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def bounded_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must be JSON serializable") from exc
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload exceeds 16384 bytes")
        return value

    @model_validator(mode="after")
    def known_schema(self) -> ActivityEnvelope:
        if self.schema_version != 1:
            raise ValueError("unsupported activity schema_version")
        if self.kind in GAP_KINDS and not self.payload:
            raise ValueError("gap records require payload details")
        return self


class ActivityPage(BaseModel):
    items: list[ActivityEnvelope]
    next_cursor: int | None


class ActivityStore:
    """Append-only event log; dedupe conflicts are exposed as HTTP 409."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else Path(
                os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR", str(Path("~/.config/runner-dashboard").expanduser()))
            ).expanduser()
            / "activity.sqlite3"
        )
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with _initialization_lock:
            conn = self._connection()
            conn.execute(
                "CREATE TABLE IF NOT EXISTS activity_events ("
                "cursor INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, "
                "session_id TEXT NOT NULL, seq INTEGER NOT NULL, event_id TEXT NOT NULL UNIQUE, "
                "envelope TEXT NOT NULL, UNIQUE(source, session_id, seq))"
            )
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS activity_no_update BEFORE UPDATE ON activity_events "
                "BEGIN SELECT RAISE(ABORT, 'activity log is append-only'); END"
            )
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS activity_no_delete BEFORE DELETE ON activity_events "
                "BEGIN SELECT RAISE(ABORT, 'activity log is append-only'); END"
            )
            conn.close()

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def append(self, event: ActivityEnvelope) -> ActivityEnvelope:
        """Insert once; an identical retry returns its durable original record."""
        if not isinstance(event, ActivityEnvelope):
            raise TypeError("event must be an ActivityEnvelope")
        encoded = event.model_dump_json()
        with self._lock:
            conn = self._connection()
            try:
                conn.execute(
                    "INSERT INTO activity_events(source,session_id,seq,event_id,envelope) VALUES(?,?,?,?,?)",
                    (event.source, event.session_id, event.seq, event.event_id, encoded),
                )
                row = conn.execute(
                    "SELECT envelope FROM activity_events WHERE source=? AND session_id=? AND seq=?",
                    (event.source, event.session_id, event.seq),
                ).fetchone()
            except sqlite3.IntegrityError as exc:
                row = conn.execute(
                    "SELECT envelope FROM activity_events WHERE source=? AND session_id=? AND seq=?",
                    (event.source, event.session_id, event.seq),
                ).fetchone()
                if row is None or row["envelope"] != encoded:
                    raise HTTPException(
                        status_code=409, detail="activity sequence conflicts with stored event"
                    ) from exc
            finally:
                conn.close()
        return ActivityEnvelope.model_validate_json(row["envelope"])

    def read(
        self,
        *,
        since: int = 0,
        repo: str | None = None,
        node: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> ActivityPage:
        if since < 0 or not 1 <= limit <= 500:
            raise ValueError("since must be nonnegative and limit must be between 1 and 500")
        clauses: list[str] = ["cursor > ?"]
        params: list[int | str] = [since]
        for column, value in (("repo", repo), ("node", node), ("session_id", session_id)):
            if value is not None:
                if column == "repo":
                    clauses.append("json_extract(envelope, '$.repo') = ?")
                else:
                    clauses.append(f"json_extract(envelope, '$.{column}') = ?")
                params.append(value)
        with self._lock:
            conn = self._connection()
            try:
                rows = conn.execute(
                    f"SELECT cursor,envelope FROM activity_events WHERE {' AND '.join(clauses)} "
                    "ORDER BY cursor LIMIT ?",
                    (*params, limit + 1),
                ).fetchall()
            finally:
                conn.close()
        more = len(rows) > limit
        rows = rows[:limit]
        items = [ActivityEnvelope.model_validate_json(row["envelope"]) for row in rows]
        return ActivityPage(items=items, next_cursor=int(rows[-1]["cursor"]) if more and rows else None)


_store: ActivityStore | None = None


def get_activity_store() -> ActivityStore:
    global _store
    if _store is None:
        _store = ActivityStore()
    return _store


def reset_activity_store() -> None:
    global _store
    _store = None


def activity_json_schema() -> dict[str, Any]:
    """Return the canonical JSON Schema for clients and generated docs."""
    return ActivityEnvelope.model_json_schema()
