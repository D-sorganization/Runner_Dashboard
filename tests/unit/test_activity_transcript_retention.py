"""Transcript chunk retention, RetentionGap signalling, and metrics (#2876)."""

from __future__ import annotations

import gzip
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from activity import ActivityEnvelope, ActivityStore
from activity_transcript import (
    ActivityRetentionSettings,
    TranscriptChunkStore,
    activity_metrics_snapshot,
    archive_activity_metadata,
    record_ingest,
    record_reject,
    reset_activity_metrics,
    run_transcript_retention,
)


def _ts(days_ago: float = 0.0) -> float:
    return (datetime.now(UTC) - timedelta(days=days_ago)).timestamp()


def _meta(**changes: object) -> ActivityEnvelope:
    base: dict[str, object] = {
        "schema_version": 1,
        "event_id": "meta-1",
        "source": "recorder",
        "principal": "agent-a",
        "node": "node-a",
        "session_id": "session-a",
        "seq": 1,
        "ts": "2026-09-01T00:00:00Z",
        "kind": "output",
        "redaction_version": 1,
        "payload": {"text": "x"},
    }
    base.update(changes)
    return ActivityEnvelope.model_validate(base)


@pytest.fixture
def stores(tmp_path: Path):
    reset_activity_metrics()
    return ActivityStore(tmp_path / "activity.sqlite3"), TranscriptChunkStore(
        tmp_path / "spool", tmp_path / "chunks.sqlite3"
    )


def _put(
    chunks: TranscriptChunkStore,
    *,
    session_id: str = "session-a",
    seq: int = 1,
    node: str = "node-a",
    body: bytes = b"x",
    stored_at: float,
) -> None:
    chunks.put_chunk(session_id=session_id, seq=seq, node=node, principal="agent-a", body=body, stored_at=stored_at)


def test_default_retention_settings_never_delete_chunks(stores) -> None:
    _, chunks = stores
    _put(chunks, body=b"hello", stored_at=_ts(200))
    assert run_transcript_retention(chunks, stores[0], ActivityRetentionSettings()).chunks_removed == 0


def test_age_prune_deletes_bytes_and_emits_retention_gap(stores) -> None:
    activity, chunks = stores
    _put(chunks, body=b"old-bytes", stored_at=_ts(45))
    _put(chunks, seq=2, body=b"fresh", stored_at=_ts(1))
    run_transcript_retention(chunks, activity, ActivityRetentionSettings(transcript_max_age_days=30), now=_ts(0))
    gaps = [item for item in activity.read(limit=20).items if item.kind == "RetentionGap"]
    assert chunks.get_chunk_body("session-a", 1) is None and gaps[0].payload["chunk_seq"] == 1


def test_disk_cap_prunes_oldest_first(stores) -> None:
    activity, chunks = stores
    _put(chunks, body=b"12345", stored_at=_ts(3))
    _put(chunks, seq=2, body=b"67890", stored_at=_ts(2))
    _put(chunks, session_id="session-b", body=b"abc", stored_at=_ts(1))
    run_transcript_retention(chunks, activity, ActivityRetentionSettings(spool_max_bytes=10), now=_ts(0))
    assert chunks.get_chunk_body("session-a", 1) is None and chunks.get_chunk_body("session-a", 2) == b"67890"


def test_metadata_archive_and_metrics(stores, tmp_path: Path) -> None:
    activity, chunks = stores
    activity.append(_meta())
    archived = archive_activity_metadata(
        activity, retention_days=7, archive_dir=tmp_path / "archives", reference_ts=_ts(0)
    )
    paths = archived[1]
    with gzip.open(paths[0], "rt", encoding="utf-8") as handle:
        assert "session-a" in handle.read()
    _put(chunks, body=b"payload", stored_at=_ts(0))
    record_ingest(events=1, bytes=4)
    record_reject(413)
    assert activity_metrics_snapshot(chunks)["reject_413"] == 1


def test_node_spool_cap_only_affects_that_node(stores) -> None:
    activity, chunks = stores
    _put(chunks, body=b"12345", stored_at=_ts(2))
    _put(chunks, session_id="session-b", node="node-b", body=b"12345", stored_at=_ts(1))
    run_transcript_retention(
        chunks, activity, ActivityRetentionSettings(node_spool_max_bytes={"node-a": 4}), now=_ts(0)
    )
    assert chunks.get_chunk_body("session-a", 1) is None and chunks.get_chunk_body("session-b", 1)


def test_append_only_activity_metadata_is_never_deleted(stores) -> None:
    activity, chunks = stores
    activity.append(_meta(ts="2026-10-27T12:00:00Z", payload={}))
    _put(chunks, stored_at=_ts(5))
    run_transcript_retention(chunks, activity, ActivityRetentionSettings(transcript_max_age_days=1), now=_ts(0))
    with sqlite3.connect(activity.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0] == 2
