"""Versioned fleet activity metadata envelope and append-only SQLite log."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from staff.redaction import redact_sensitive_content

GAP_KINDS = ("OverflowGap", "RetentionGap", "CaptureGap")
MAX_PAYLOAD_BYTES = 16_384
MAX_BATCH_BYTES = 262_144
MAX_BATCH_EVENTS = 100
MAX_SCAN_LIMIT = 10_000
REDACTION_VERSION = 1
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
            conn.execute(
                "CREATE TABLE IF NOT EXISTS activity_ingestion_usage ("
                "principal TEXT NOT NULL, ts REAL NOT NULL, events INTEGER NOT NULL, bytes INTEGER NOT NULL)"
            )
            # Indexes so scan_recent's node/repo filters never fall back to a full scan.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS activity_events_node_idx "
                "ON activity_events(json_extract(envelope, '$.node'))"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS activity_events_repo_idx "
                "ON activity_events(json_extract(envelope, '$.repo'))"
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

    def ingest(
        self,
        events: list[ActivityEnvelope],
        *,
        principal: str,
        max_events: int = 1000,
        max_bytes: int = 1_048_576,
        now: float | None = None,
    ) -> list[ActivityEnvelope]:
        """Bind sources (type + node) and sessions on first write; commit a batch once.

        Quotas count new redacted events in a rolling 60-second window. Retries
        consume no quota. SQLite serializes binding, dedupe, quota and commit
        across processes; any failure rolls the entire batch back.
        """
        if not principal or max_events < 1 or max_bytes < 1:
            raise ValueError("principal and positive quota limits are required")
        if not events or len(events) > MAX_BATCH_EVENTS:
            raise HTTPException(413 if events else 422, "activity batch must contain 1..100 events")
        if sum(len(event.model_dump_json().encode()) for event in events) > MAX_BATCH_BYTES:
            raise HTTPException(413, "activity batch exceeds byte limit")
        if any(event.principal != principal for event in events):
            raise HTTPException(403, "activity principal does not match caller")
        safe = [_redacted_event(event) for event in events]
        timestamp = time.time() if now is None else now
        with self._lock:
            conn = self._connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM activity_ingestion_usage WHERE ts <= ?", (timestamp - 60,))
                used = conn.execute(
                    "SELECT COALESCE(SUM(events),0), COALESCE(SUM(bytes),0) "
                    "FROM activity_ingestion_usage WHERE principal=?",
                    (principal,),
                ).fetchone()
                count = size = 0
                result = []
                for event in safe:
                    # Include legacy append() records in binding enforcement.
                    foreign = conn.execute(
                        "SELECT 1 FROM activity_events WHERE "
                        "(session_id=? OR (source=? AND json_extract(envelope,'$.node')=?)) "
                        "AND json_extract(envelope,'$.principal') != ? LIMIT 1",
                        (event.session_id, event.source, event.node, principal),
                    ).fetchone()
                    if foreign:
                        raise HTTPException(403, "activity source or session belongs to another principal")
                    encoded = event.model_dump_json()
                    previous = conn.execute(
                        "SELECT envelope FROM activity_events WHERE source=? AND session_id=? AND seq=?",
                        (event.source, event.session_id, event.seq),
                    ).fetchone()
                    if previous:
                        if previous["envelope"] != encoded:
                            raise HTTPException(409, "activity sequence conflicts with stored event")
                        result.append(ActivityEnvelope.model_validate_json(previous["envelope"]))
                        continue
                    count += 1
                    size += len(encoded.encode())
                    if count + used[0] > max_events or size + used[1] > max_bytes:
                        raise HTTPException(429, "activity ingestion quota exceeded", headers={"Retry-After": "60"})
                    conn.execute(
                        "INSERT INTO activity_events(source,session_id,seq,event_id,envelope) VALUES(?,?,?,?,?)",
                        (event.source, event.session_id, event.seq, event.event_id, encoded),
                    )
                    result.append(event)
                if count:
                    conn.execute(
                        "INSERT INTO activity_ingestion_usage VALUES(?,?,?,?)", (principal, timestamp, count, size)
                    )
                conn.commit()
                return result
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise HTTPException(409, "activity event id conflicts with stored event") from exc
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

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
        clauses, params = _json_filters(repo=repo, node=node, session_id=session_id)
        clauses.insert(0, "cursor > ?")
        params.insert(0, since)
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

    def scan_recent(
        self,
        *,
        node: str | None = None,
        repo: str | None = None,
        limit: int = 2000,
    ) -> list[ActivityEnvelope]:
        """Up to ``limit`` most recently ingested envelopes, newest first.

        Bounded and indexed (``cursor`` order, indexed node/repo filters) so
        this never degrades to a full scan; callers needing complete, gap-free
        history should use :meth:`read`'s cursor pagination instead.
        """
        if not 1 <= limit <= MAX_SCAN_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SCAN_LIMIT}")
        clauses, params = _json_filters(repo=repo, node=node)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            conn = self._connection()
            try:
                rows = conn.execute(
                    f"SELECT envelope FROM activity_events {where} ORDER BY cursor DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
            finally:
                conn.close()
        return [ActivityEnvelope.model_validate_json(row["envelope"]) for row in rows]


def _json_filters(
    *, repo: str | None = None, node: str | None = None, session_id: str | None = None
) -> tuple[list[str], list[int | str]]:
    """Build ``WHERE`` clauses for the handful of indexed envelope columns callers filter on."""
    clauses: list[str] = []
    params: list[int | str] = []
    for column, value in (("repo", repo), ("node", node), ("session_id", session_id)):
        if value is not None:
            clauses.append(f"json_extract(envelope, '$.{column}') = ?")
            params.append(value)
    return clauses, params


_store: ActivityStore | None = None


def _redacted_event(event: ActivityEnvelope) -> ActivityEnvelope:
    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return redact_sensitive_content(value)
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            result = {clean(key): clean(item) for key, item in sorted(value.items())}
            if len(result) != len(value):
                raise HTTPException(422, "activity keys collide after redaction")
            return result
        return value

    data = clean(event.model_dump(mode="json"))
    # Binding identifiers must be opaque, never secrets or network addresses.
    for key in ("principal", "source", "node", "session_id", "event_id"):
        if data[key] != getattr(event, key):
            raise HTTPException(422, "activity identifiers must not contain sensitive content")
    data["redaction_version"] = REDACTION_VERSION
    try:
        return ActivityEnvelope.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(422, "activity envelope invalid after redaction") from exc


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
