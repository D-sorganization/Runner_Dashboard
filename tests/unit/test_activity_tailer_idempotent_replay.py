"""Idempotent session tailer replay after checkpoint loss (#4278 J-11)."""

from __future__ import annotations

import json
from pathlib import Path

import activity_claude_tailer as act_tailer
import activity_transcript as at
import pytest
from activity import ActivityStore
@pytest.fixture
def stores(tmp_path: Path) -> tuple[ActivityStore, at.TranscriptChunkStore, Path]:
    act_tailer.reset_adapter_health()
    return (
        ActivityStore(tmp_path / "activity.sqlite3"),
        at.TranscriptChunkStore(tmp_path / "spool", tmp_path / "chunks.sqlite3"),
        tmp_path / "claude_tailer_checkpoints.json",
    )


def test_replay_without_checkpoint_does_not_409_or_duplicate_chunks(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path], tmp_path: Path
) -> None:
    """Simulate crash after first pass: empty checkpoint retry must stay idempotent."""
    activity_store, chunk_store, cp = stores
    session_file = tmp_path / "projects" / "-work" / "sess-replay.jsonl"
    line1 = json.dumps({"version": 1, "type": "user", "text": "first turn"}) + "\n"
    line2 = json.dumps({"version": 1, "type": "assistant", "text": "first reply"}) + "\n"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(line1 + line2, encoding="utf-8")

    def make_tailer() -> act_tailer.ClaudeSessionTailer:
        return act_tailer.ClaudeSessionTailer(
            roots=[tmp_path / "projects"],
            activity_store=activity_store,
            chunk_store=chunk_store,
            checkpoint_path=cp,
            node="node-a",
            enabled=True,
        )

    assert make_tailer().tail_once().chunks_written == 2
    cp.unlink(missing_ok=True)

    assert make_tailer().tail_once().chunks_written == 2
    events = activity_store.read(session_id="claude-sess-replay").items
    assert len(events) == 2
    assert [c.seq for c in chunk_store.list_chunks(session_id="claude-sess-replay")] == [1, 2]
