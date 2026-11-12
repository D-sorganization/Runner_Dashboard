"""Tests for opt-in Claude Code session JSONL tailer (#2871, epic #2856 slice T5a)."""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path
from typing import Any

import activity_claude_tailer as act_tailer
import activity_transcript as at
import pytest
from activity import REDACTION_VERSION, ActivityStore


@pytest.fixture
def stores(tmp_path: Path) -> tuple[ActivityStore, at.TranscriptChunkStore, Path]:
    at.reset_activity_metrics()
    act_tailer.reset_adapter_health()
    return (
        ActivityStore(tmp_path / "activity.sqlite3"),
        at.TranscriptChunkStore(tmp_path / "spool", tmp_path / "chunks.sqlite3"),
        tmp_path / "claude_tailer_checkpoints.json",
    )


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def _make_tailer(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path],
    roots: list[Path],
    **extra: Any,
) -> act_tailer.ClaudeSessionTailer:
    act_store, chunk_store, cp = stores
    return act_tailer.ClaudeSessionTailer(
        roots=roots, activity_store=act_store, chunk_store=chunk_store, checkpoint_path=cp, node="node-a", **extra
    )


def test_opt_in_disabled_by_default_is_noop(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activity_store, chunk_store, _ = stores
    monkeypatch.delenv("RUNNER_DASHBOARD_CLAUDE_TAILER_ENABLED", raising=False)
    _write_jsonl(
        tmp_path / "projects" / "-work-repo" / "session-disabled.jsonl",
        [json.dumps({"version": 1, "type": "user", "message": {"role": "user", "content": "hello"}}) + "\n"],
    )
    res = _make_tailer(stores, [tmp_path / "projects"]).tail_once()
    assert res.enabled is False and res.chunks_written == 0 and res.gaps_emitted == 0
    assert chunk_store.list_chunks() == [] and not activity_store.has_any_events()


def test_versioned_parser_ingests_v1_cli_and_desktop_jsonl_with_redaction_before_spool(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path], tmp_path: Path
) -> None:
    activity_store, chunk_store, _ = stores
    cli_root, desktop_root = tmp_path / "cli_projects", tmp_path / "desktop_sessions"
    tok, priv_ip = "".join(["gh", "p_", "b" * 36]), ".".join(["10", "55", "12", "8"])

    u_rec = {
        "version": 1,
        "type": "user",
        "sessionId": "cli-01",
        "message": {"role": "user", "content": f"Auth {tok} {priv_ip}"},
    }
    a_content = [
        {"type": "text", "text": "Check"},
        {"type": "tool_use", "name": "bash", "input": {"cmd": f"curl {priv_ip}"}},
    ]
    a_rec = {
        "schema_version": 1,
        "type": "assistant",
        "sessionId": "cli-01",
        "message": {"role": "assistant", "content": a_content},
    }
    _write_jsonl(cli_root / "-ws" / "cli-01.jsonl", [json.dumps(u_rec) + "\n", json.dumps(a_rec) + "\n"])
    d_rec = {"format_version": "v1", "type": "summary", "session_id": "claude-desk-02", "text": f"Summary {tok}"}
    _write_jsonl(desktop_root / "desk-02.jsonl", [json.dumps(d_rec) + "\n"])

    res = _make_tailer(stores, [cli_root, desktop_root], enabled=True).tail_once()
    assert res.enabled is True and res.chunks_written == 3 and res.gaps_emitted == 0 and res.warnings == ()
    assert [c.seq for c in chunk_store.list_chunks(session_id="claude-cli-01")] == [1, 2]

    for ref in chunk_store.list_chunks():
        body = (chunk_store.get_chunk_body(ref.session_id, ref.seq) or b"").decode("utf-8")
        assert tok not in body and priv_ip not in body and "REDACTED" in body
    for gz_path in chunk_store.spool_dir.rglob("*.gz"):
        with gzip.open(gz_path, "rt", encoding="utf-8") as handle:
            disk_text = handle.read()
        assert tok not in disk_text and priv_ip not in disk_text

    events = activity_store.read(session_id="claude-cli-01").items
    assert len(events) == 2 and all(
        e.source == "desktop_adapter" and e.redaction_version == REDACTION_VERSION for e in events
    )


def test_unknown_format_version_and_malformed_lines_emit_capture_gap_and_health_warning(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    activity_store, chunk_store, _ = stores
    lines = [
        json.dumps({"version": 1, "type": "user", "text": "line one valid"}) + "\n",
        json.dumps({"format_version": 2, "type": "assistant", "text": "future v2 record"}) + "\n",
        "{corrupt-json-line\n",
        json.dumps({"version": 1, "unexpected_shape": True}) + "\n",
        json.dumps({"version": 1, "type": "assistant", "text": "line five valid"}) + "\n",
    ]
    _write_jsonl(tmp_path / "projects" / "-work" / "sess-gaps.jsonl", lines)
    with caplog.at_level(logging.WARNING, logger="dashboard.activity_claude_tailer"):
        res = _make_tailer(stores, [tmp_path / "projects"], enabled=True).tail_once()

    expected_reasons = ["unsupported_format_version", "malformed_record", "unrecognized_record_format"]
    assert res.chunks_written == 2 and res.gaps_emitted == 3 and [w.reason for w in res.warnings] == expected_reasons
    assert any("unsupported_format_version" in rec.message for rec in caplog.records)
    assert [c.seq for c in chunk_store.list_chunks(session_id="claude-sess-gaps")] == [1, 5]

    gaps = [e for e in activity_store.read(session_id="claude-sess-gaps").items if e.kind == "CaptureGap"]
    assert {(g.payload["chunk_seq"], g.payload["reason"]) for g in gaps} == {
        (2, expected_reasons[0]),
        (3, expected_reasons[1]),
        (4, expected_reasons[2]),
    }
    snap = act_tailer.adapter_health_snapshot()
    assert snap["warnings_total"] == 3 and snap["unsupported_version_total"] == 1


def test_offset_checkpointing_survives_restarts_without_duplicates_and_waits_for_newline(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path], tmp_path: Path
) -> None:
    _, chunk_store, _ = stores
    session_file = tmp_path / "projects" / "-work" / "sess-restart.jsonl"
    line1 = json.dumps({"version": 1, "type": "user", "text": "first turn"}) + "\n"
    line2 = json.dumps({"version": 1, "type": "assistant", "text": "first reply"}) + "\n"
    partial3 = json.dumps({"version": 1, "type": "user", "text": "second turn"})
    _write_jsonl(session_file, [line1, line2, partial3])

    assert _make_tailer(stores, [tmp_path / "projects"], enabled=True).tail_once().chunks_written == 2
    assert _make_tailer(stores, [tmp_path / "projects"], enabled=True).tail_once().chunks_written == 0
    assert [c.seq for c in chunk_store.list_chunks(session_id="claude-sess-restart")] == [1, 2]

    line4 = json.dumps({"version": 1, "type": "assistant", "text": "second reply"}) + "\n"
    _write_jsonl(session_file, [line1, line2, partial3 + "\n", line4])
    assert _make_tailer(stores, [tmp_path / "projects"], enabled=True).tail_once().chunks_written == 2
    assert [c.seq for c in chunk_store.list_chunks(session_id="claude-sess-restart")] == [1, 2, 3, 4]


def test_dbc_preconditions_reject_invalid_or_sensitive_identifiers(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path], tmp_path: Path
) -> None:
    activity_store, chunk_store, checkpoint_path = stores
    bad_tok = "".join(["gh", "p_", "c" * 36])
    with pytest.raises(ValueError, match="node"):
        act_tailer.ClaudeSessionTailer(
            roots=[tmp_path], activity_store=activity_store, chunk_store=chunk_store, node="", enabled=True
        )
    with pytest.raises(ValueError, match="sensitive"):
        act_tailer.ClaudeSessionTailer(
            roots=[tmp_path], activity_store=activity_store, chunk_store=chunk_store, node=bad_tok, enabled=True
        )

    checkpoint_path.write_text(json.dumps({"schema_version": 999, "files": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        _make_tailer(stores, [tmp_path], enabled=True).tail_once()
