"""Transcript chunk spool, retention sweeps, and in-process activity metrics (#2866, #2876)."""

from __future__ import annotations

import gzip
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from activity import REDACTION_VERSION, ActivityEnvelope, ActivityStore

DEFAULT_METADATA_ARCHIVE_DAYS = 180
DEFAULT_MAX_CHUNK_BYTES = 65_536
DEFAULT_SESSION_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_NODE_MAX_BYTES = 128 * 1024 * 1024
_ANSI_RE = re.compile(r"(\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_])")
_METRICS_LOCK = threading.Lock()
_METRICS: dict[str, int] = {
    "ingest_events": 0,
    "ingest_bytes": 0,
    "reject_413": 0,
    "reject_429": 0,
    "reject_409": 0,
    "gaps_total": 0,
}


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def redact_terminal_stream(text: str) -> str:
    from staff.redaction import redact_sensitive_content

    parts = _ANSI_RE.split(text)
    redacted = "".join(part if idx % 2 else redact_sensitive_content(part) for idx, part in enumerate(parts))
    plain = strip_ansi(redacted)
    return redact_sensitive_content(plain) if redact_sensitive_content(plain) != plain else redacted


def reset_activity_metrics() -> None:
    with _METRICS_LOCK:
        for key in _METRICS:
            _METRICS[key] = 0


def record_ingest(*, events: int = 1, bytes: int = 0) -> None:
    with _METRICS_LOCK:
        _METRICS["ingest_events"] += events
        _METRICS["ingest_bytes"] += bytes


def record_reject(status_code: int) -> None:
    with _METRICS_LOCK:
        _METRICS[f"reject_{status_code}"] += 1


def record_gap() -> None:
    with _METRICS_LOCK:
        _METRICS["gaps_total"] += 1


@dataclass(frozen=True)
class TranscriptChunkRef:
    session_id: str
    seq: int
    node: str
    principal: str
    stored_at: float
    byte_len: int


def _where_clause(node: str | None = None, session_id: str | None = None) -> tuple[str, tuple[str, ...]]:
    clauses = [f"{col}=?" for col, val in (("node", node), ("session_id", session_id)) if val is not None]
    params = tuple(val for val in (node, session_id) if val is not None)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


class TranscriptChunkStore:
    def __init__(self, spool_dir: str | Path, db_path: str | Path) -> None:
        self.spool_dir = Path(spool_dir)
        self.db_path = Path(db_path)
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._lock, self._connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS transcript_chunks ("
                "session_id TEXT NOT NULL, seq INTEGER NOT NULL, node TEXT NOT NULL, "
                "principal TEXT NOT NULL, stored_at REAL NOT NULL, byte_len INTEGER NOT NULL, "
                "rel_path TEXT NOT NULL, PRIMARY KEY(session_id, seq))"
            )

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def put_chunk(
        self, *, session_id: str, seq: int, node: str, principal: str, body: bytes, stored_at: float
    ) -> TranscriptChunkRef:
        if not session_id or seq < 0 or not node or not principal or stored_at <= 0 or not body:
            raise ValueError("invalid transcript chunk")
        rel = Path(session_id.replace("/", "_")) / f"{seq}.json.gz"
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT node, principal, stored_at, rel_path, byte_len "
                    "FROM transcript_chunks WHERE session_id=? AND seq=?",
                    (session_id, seq),
                ).fetchone()
                if row:
                    current = self._read(Path(row["rel_path"]))
                    if current != body or row["node"] != node or row["principal"] != principal:
                        record_reject(409)
                        raise ValueError("conflicting transcript chunk payload")
                    return TranscriptChunkRef(
                        session_id, seq, node, principal, float(row["stored_at"]), int(row["byte_len"])
                    )
                path = self.spool_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                with gzip.open(path, "wb") as handle:
                    handle.write(body)
                conn.execute(
                    "INSERT INTO transcript_chunks VALUES(?,?,?,?,?,?,?)",
                    (session_id, seq, node, principal, stored_at, len(body), str(rel)),
                )
        record_ingest(bytes=len(body))
        return TranscriptChunkRef(session_id, seq, node, principal, stored_at, len(body))

    def _read(self, rel: Path) -> bytes | None:
        path = self.spool_dir / rel
        if not path.is_file():
            return None
        with gzip.open(path, "rb") as handle:
            return handle.read()

    def get_chunk_body(self, session_id: str, seq: int) -> bytes | None:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT rel_path FROM transcript_chunks WHERE session_id=? AND seq=?", (session_id, seq)
            ).fetchone()
        return None if row is None else self._read(Path(row["rel_path"]))

    def list_chunks(self, *, node: str | None = None, session_id: str | None = None) -> list[TranscriptChunkRef]:
        clause, params = _where_clause(node=node, session_id=session_id)
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                f"SELECT session_id,seq,node,principal,stored_at,byte_len FROM transcript_chunks {clause} "
                "ORDER BY stored_at ASC, seq ASC",
                params,
            ).fetchall()
        return [TranscriptChunkRef(r[0], int(r[1]), r[2], r[3], float(r[4]), int(r[5])) for r in rows]

    def delete_chunk(self, session_id: str, seq: int) -> int:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT rel_path, byte_len FROM transcript_chunks WHERE session_id=? AND seq=?",
                (session_id, seq),
            ).fetchone()
            if row is None:
                return 0
            conn.execute("DELETE FROM transcript_chunks WHERE session_id=? AND seq=?", (session_id, seq))
        path = self.spool_dir / Path(row["rel_path"])
        if path.is_file():
            path.unlink()
        return int(row["byte_len"])

    def spool_bytes(self, *, node: str | None = None, session_id: str | None = None) -> int:
        clause, params = _where_clause(node=node, session_id=session_id)
        with self._lock, self._connection() as conn:
            row = conn.execute(
                f"SELECT COALESCE(SUM(byte_len),0) FROM transcript_chunks {clause}",
                params,
            ).fetchone()[0]
            return int(row)

    def spool_depth(self, *, node: str | None = None, session_id: str | None = None) -> int:
        clause, params = _where_clause(node=node, session_id=session_id)
        with self._lock, self._connection() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM transcript_chunks {clause}", params).fetchone()[0])


def emit_capture_gap(
    activity: ActivityStore,
    session_id: str,
    chunk_seq: int,
    principal: str,
    node: str,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> None:
    with sqlite3.connect(str(activity.db_path)) as conn:
        if conn.execute(
            "SELECT 1 FROM activity_events WHERE session_id=? "
            "AND json_extract(envelope,'$.kind') IN ('CaptureGap','RetentionGap') "
            "AND json_extract(envelope,'$.payload.chunk_seq')=? LIMIT 1",
            (session_id, chunk_seq),
        ).fetchone():
            return
        max_seq = int(
            conn.execute(
                "SELECT COALESCE(MAX(seq), -1) FROM activity_events WHERE source='recorder' AND session_id=?",
                (session_id,),
            ).fetchone()[0]
        )
    activity.append(
        ActivityEnvelope.model_validate(
            {
                "schema_version": 1,
                "event_id": f"capture-gap:{session_id}:{chunk_seq}:{reason}",
                "source": "recorder",
                "principal": principal,
                "node": node,
                "session_id": session_id,
                "seq": max(1_000_000 + chunk_seq, max_seq + 1),
                "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "kind": "CaptureGap",
                "redaction_version": REDACTION_VERSION,
                "payload": {"reason": reason, "chunk_seq": chunk_seq, **(extra or {})},
            }
        )
    )
    record_gap()


@dataclass(frozen=True)
class ActivityRetentionSettings:
    transcript_max_age_days: int = 0
    spool_max_bytes: int = 0
    node_spool_max_bytes: dict[str, int] = field(default_factory=dict)
    metadata_archive_days: int = DEFAULT_METADATA_ARCHIVE_DAYS

    @classmethod
    def from_env(cls) -> ActivityRetentionSettings:
        def _int(name: str, default: int) -> int:
            raw = os.environ.get(name, "").strip()
            return default if not raw else max(0, int(raw))

        raw_nodes = os.environ.get("RUNNER_DASHBOARD_ACTIVITY_NODE_SPOOL_MAX_BYTES", "").strip()
        node_caps = {str(k): max(0, int(v)) for k, v in json.loads(raw_nodes).items()} if raw_nodes else {}
        return cls(
            transcript_max_age_days=_int("RUNNER_DASHBOARD_ACTIVITY_TRANSCRIPT_MAX_AGE_DAYS", 0),
            spool_max_bytes=_int("RUNNER_DASHBOARD_ACTIVITY_SPOOL_MAX_BYTES", 0),
            node_spool_max_bytes=node_caps,
            metadata_archive_days=_int(
                "RUNNER_DASHBOARD_ACTIVITY_METADATA_ARCHIVE_DAYS", DEFAULT_METADATA_ARCHIVE_DAYS
            ),
        )


@dataclass(frozen=True)
class RetentionSweepResult:
    chunks_removed: int = 0
    archived_metadata_rows: int = 0


def archive_activity_metadata(
    store: ActivityStore,
    *,
    retention_days: int = DEFAULT_METADATA_ARCHIVE_DAYS,
    archive_dir: Path | None = None,
    reference_ts: float | None = None,
) -> tuple[int, list[Path]]:
    if retention_days < 1:
        return 0, []
    ref = datetime.fromtimestamp(reference_ts, tz=UTC) if reference_ts is not None else datetime.now(UTC)
    cutoff = (ref - timedelta(days=retention_days)).isoformat().replace("+00:00", "Z")
    out_dir = (archive_dir or _config_root() / "archives" / "activity").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / f"activity_metadata_{ref.strftime('%Y%m')}.jsonl.gz"
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT envelope FROM activity_events WHERE json_extract(envelope, '$.ts') < ? ORDER BY cursor ASC",
            (cutoff,),
        ).fetchall()
    if not rows:
        return 0, []
    with gzip.open(archive_path, "at", encoding="utf-8") as handle:
        for row in rows:
            handle.write(row["envelope"] + "\n")
    return len(rows), [archive_path]


def run_transcript_retention(
    chunk_store: TranscriptChunkStore,
    activity_store: ActivityStore,
    settings: ActivityRetentionSettings,
    *,
    archive_dir: Path | None = None,
    now: float | None = None,
) -> RetentionSweepResult:
    if chunk_store is None or activity_store is None:
        raise ValueError("chunk and activity stores are required")
    ref = datetime.fromtimestamp(now, tz=UTC) if now is not None else datetime.now(UTC)
    archived = 0
    if settings.metadata_archive_days > 0:
        archived, _ = archive_activity_metadata(
            activity_store,
            retention_days=settings.metadata_archive_days,
            archive_dir=archive_dir,
            reference_ts=now,
        )
    removed = 0
    if settings.transcript_max_age_days > 0:
        cutoff_ts = (ref - timedelta(days=settings.transcript_max_age_days)).timestamp()
        for chunk in list(chunk_store.list_chunks()):
            if chunk.stored_at < cutoff_ts and _remove_chunk(chunk_store, activity_store, chunk, "age"):
                removed += 1
    if settings.spool_max_bytes > 0:
        pruned, _ = _enforce_byte_cap(chunk_store, activity_store, limit=settings.spool_max_bytes, node=None)
        removed += pruned
    for node, limit in settings.node_spool_max_bytes.items():
        if limit <= 0:
            continue
        removed += _enforce_byte_cap(chunk_store, activity_store, limit=limit, node=node)[0]
    return RetentionSweepResult(removed, archived)


def _remove_chunk(store: TranscriptChunkStore, activity: ActivityStore, ref: TranscriptChunkRef, reason: str) -> bool:
    deleted = store.delete_chunk(ref.session_id, ref.seq)
    if deleted <= 0:
        return False
    source = "recorder"
    with sqlite3.connect(str(activity.db_path)) as conn:
        gap_seq = (
            int(
                conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) FROM activity_events WHERE source=? AND session_id=?",
                    (source, ref.session_id),
                ).fetchone()[0]
            )
            + 1
        )
    activity.append(
        ActivityEnvelope.model_validate(
            {
                "schema_version": 1,
                "event_id": f"retention-{uuid4()}",
                "source": source,
                "principal": ref.principal,
                "node": ref.node,
                "session_id": ref.session_id,
                "seq": gap_seq,
                "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "kind": "RetentionGap",
                "redaction_version": 1,
                "payload": {"reason": reason, "bytes": deleted, "stored_at": ref.stored_at, "chunk_seq": ref.seq},
            }
        )
    )
    record_gap()
    return True


def _enforce_byte_cap(
    chunk_store: TranscriptChunkStore, activity_store: ActivityStore, *, limit: int, node: str | None
) -> tuple[int, int]:
    removed = gaps = 0
    while chunk_store.spool_bytes(node=node) > limit:
        chunks = chunk_store.list_chunks(node=node)
        if not chunks or not _remove_chunk(chunk_store, activity_store, chunks[0], "disk_cap"):
            break
        removed += 1
        gaps += 1
    return removed, gaps


def _config_root() -> Path:
    return Path(
        os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR", str(Path("~/.config/runner-dashboard").expanduser()))
    ).expanduser()


def activity_metrics_snapshot(chunk_store: TranscriptChunkStore | None = None) -> dict[str, Any]:
    target = chunk_store or get_transcript_chunk_store()
    with _METRICS_LOCK:
        snap = dict(_METRICS)
    snap["store_bytes"] = target.spool_bytes()
    snap["spool_depth"] = target.spool_depth()
    return snap


_store: TranscriptChunkStore | None = None


def get_transcript_chunk_store() -> TranscriptChunkStore:
    global _store
    if _store is None:
        root = _config_root()
        _store = TranscriptChunkStore(root / "activity-transcript-spool", root / "activity_transcripts.sqlite3")
    return _store
