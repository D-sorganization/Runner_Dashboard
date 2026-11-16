"""Distinct activity source bindings for Claude and Codex tailers (#4278 J-10)."""

from __future__ import annotations

import json
from pathlib import Path

import activity_claude_tailer as claude_tailer
import activity_codex_tailer as codex_tailer
import activity_transcript as at
import pytest
from activity import ActivityStore


@pytest.fixture
def stores(tmp_path: Path) -> tuple[ActivityStore, at.TranscriptChunkStore, Path, Path]:
    claude_tailer.reset_adapter_health()
    codex_tailer.reset_adapter_health()
    cp_claude = tmp_path / "claude_tailer_checkpoints.json"
    cp_codex = tmp_path / "codex_tailer_checkpoints.json"
    return (
        ActivityStore(tmp_path / "activity.sqlite3"),
        at.TranscriptChunkStore(tmp_path / "spool", tmp_path / "chunks.sqlite3"),
        cp_claude,
        cp_codex,
    )


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def test_claude_and_codex_tailers_share_node_without_binding_403(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path, Path], tmp_path: Path
) -> None:
    """J-10: both providers must ingest on the same node without desktop_adapter collision."""
    activity_store, chunk_store, cp_claude, cp_codex = stores
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    claude_line = json.dumps({"version": 1, "type": "user", "sessionId": "c-1", "text": "claude turn"}) + "\n"
    codex_line = json.dumps({"version": 1, "type": "user", "sessionId": "x-1", "text": "codex turn"}) + "\n"
    _write_jsonl(claude_root / "sess.jsonl", [claude_line])
    _write_jsonl(codex_root / "sess.jsonl", [codex_line])

    node = "shared-node"
    claude = claude_tailer.ClaudeSessionTailer(
        roots=[claude_root],
        activity_store=activity_store,
        chunk_store=chunk_store,
        checkpoint_path=cp_claude,
        node=node,
        enabled=True,
    )
    codex = codex_tailer.CodexSessionTailer(
        roots=[codex_root],
        activity_store=activity_store,
        chunk_store=chunk_store,
        checkpoint_path=cp_codex,
        node=node,
        enabled=True,
    )
    assert claude.tail_once().chunks_written == 1
    assert codex.tail_once().chunks_written == 1

    claude_events = activity_store.read(session_id="claude-c-1").items
    codex_events = activity_store.read(session_id="codex-x-1").items
    assert len(claude_events) == 1 and len(codex_events) == 1
    assert claude_events[0].source == "claude_desktop_adapter"
    assert codex_events[0].source == "codex_desktop_adapter"
    assert claude_events[0].source != codex_events[0].source
