"""Tests for opt-in Codex session JSONL tailer (#2872, epic #2856 slice T5b)."""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path
from typing import Any

import activity_claude_tailer as claude_tailer
import activity_codex_tailer as codex_tailer
import activity_transcript as at
import pytest
from activity import REDACTION_VERSION, ActivityStore


@pytest.fixture
def stores(tmp_path: Path) -> tuple[ActivityStore, at.TranscriptChunkStore, Path]:
    at.reset_activity_metrics()
    codex_tailer.reset_adapter_health()
    return (
        ActivityStore(tmp_path / "activity.sqlite3"),
        at.TranscriptChunkStore(tmp_path / "spool", tmp_path / "chunks.sqlite3"),
        tmp_path / "codex_tailer_checkpoints.json",
    )


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def _make_tailer(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path],
    roots: list[Path],
    **extra: Any,
) -> codex_tailer.CodexSessionTailer:
    act_store, chunk_store, cp = stores
    return codex_tailer.CodexSessionTailer(
        roots=roots, activity_store=act_store, chunk_store=chunk_store, checkpoint_path=cp, node="node-a", **extra
    )


def test_codex_tailer_shares_t5a_tailer_core_and_opt_in_disabled_by_default(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert issubclass(codex_tailer.CodexSessionTailer, claude_tailer.ClaudeSessionTailer)
    activity_store, chunk_store, _ = stores
    monkeypatch.delenv("RUNNER_DASHBOARD_CODEX_TAILER_ENABLED", raising=False)
    _write_jsonl(
        tmp_path / "sessions" / "2026" / "10" / "29" / "rollout-disabled.jsonl",
        [json.dumps({"version": 1, "type": "user", "text": "hello codex"}) + "\n"],
    )
    res = _make_tailer(stores, [tmp_path / "sessions"]).tail_once()
    assert res.enabled is False and res.chunks_written == 0 and res.gaps_emitted == 0
    assert chunk_store.list_chunks() == [] and not activity_store.has_any_events()


def test_versioned_parser_ingests_codex_cli_and_desktop_logs_with_redaction_before_spool(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path], tmp_path: Path
) -> None:
    activity_store, chunk_store, _ = stores
    cli_root, desktop_root = tmp_path / "codex_cli_sessions", tmp_path / "codex_desktop_sessions"
    tok, priv_ip = "".join(["gh", "p_", "d" * 36]), ".".join(["10", "66", "14", "9"])

    cli_lines = [
        json.dumps(
            {
                "version": 1,
                "type": "response_item",
                "session_id": "cli-77",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": f"Deploy with {tok} to {priv_ip}"}],
                },
            }
        )
        + "\n",
        json.dumps(
            {
                "schema_version": 1,
                "type": "response_item",
                "session_id": "cli-77",
                "payload": {
                    "type": "function_call",
                    "name": "shell",
                    "arguments": f'{{"cmd":"ssh {priv_ip}"}}',
                },
            }
        )
        + "\n",
    ]
    _write_jsonl(cli_root / "2026" / "10" / "29" / "cli-77.jsonl", cli_lines)

    desk_rec = {
        "format_version": "v1",
        "type": "event_msg",
        "sessionId": "codex-desk-88",
        "payload": {"type": "agent_message", "message": f"Completed check for {tok}"},
    }
    _write_jsonl(desktop_root / "desk-88.jsonl", [json.dumps(desk_rec) + "\n"])

    res = _make_tailer(stores, [cli_root, desktop_root], enabled=True).tail_once()
    assert res.enabled is True and res.chunks_written == 3 and res.gaps_emitted == 0 and res.warnings == ()
    assert [c.seq for c in chunk_store.list_chunks(session_id="codex-cli-77")] == [1, 2]
    assert [c.seq for c in chunk_store.list_chunks(session_id="codex-desk-88")] == [1]

    for ref in chunk_store.list_chunks():
        body = (chunk_store.get_chunk_body(ref.session_id, ref.seq) or b"").decode("utf-8")
        assert tok not in body and priv_ip not in body and "REDACTED" in body
    for gz_path in chunk_store.spool_dir.rglob("*.gz"):
        with gzip.open(gz_path, "rt", encoding="utf-8") as handle:
            disk_text = handle.read()
        assert tok not in disk_text and priv_ip not in disk_text

    events = activity_store.read(session_id="codex-cli-77").items
    assert len(events) == 2 and all(
        e.source == "desktop_adapter"
        and e.redaction_version == REDACTION_VERSION
        and e.payload.get("adapter") == "codex"
        for e in events
    )


def test_unknown_versions_and_malformed_lines_emit_capture_gap_and_health_warning(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    activity_store, chunk_store, _ = stores
    lines = [
        json.dumps({"version": 1, "type": "user", "text": "line one valid"}) + "\n",
        json.dumps({"format_version": 2, "type": "assistant", "text": "unsupported v2"}) + "\n",
        "{corrupt-codex-json\n",
        json.dumps({"version": 1, "unknown_codex_key": True}) + "\n",
        json.dumps({"version": 1, "type": "assistant", "text": "line five valid"}) + "\n",
    ]
    _write_jsonl(tmp_path / "sessions" / "sess-gaps.jsonl", lines)
    with caplog.at_level(logging.WARNING, logger="dashboard.activity_codex_tailer"):
        res = _make_tailer(stores, [tmp_path / "sessions"], enabled=True).tail_once()

    expected_reasons = ["unsupported_format_version", "malformed_record", "unrecognized_record_format"]
    assert res.chunks_written == 2 and res.gaps_emitted == 3 and [w.reason for w in res.warnings] == expected_reasons
    assert any("unsupported_format_version" in rec.message for rec in caplog.records)
    assert [c.seq for c in chunk_store.list_chunks(session_id="codex-sess-gaps")] == [1, 5]

    gaps = [e for e in activity_store.read(session_id="codex-sess-gaps").items if e.kind == "CaptureGap"]
    assert {(g.payload["chunk_seq"], g.payload["reason"], g.payload["adapter"]) for g in gaps} == {
        (2, expected_reasons[0], "codex"),
        (3, expected_reasons[1], "codex"),
        (4, expected_reasons[2], "codex"),
    }
    snap = codex_tailer.adapter_health_snapshot()
    assert snap["warnings_total"] == 3 and snap["unsupported_version_total"] == 1


def test_restart_safe_checkpointing_and_dbc_validation(
    stores: tuple[ActivityStore, at.TranscriptChunkStore, Path], tmp_path: Path
) -> None:
    activity_store, chunk_store, checkpoint_path = stores
    session_file = tmp_path / "sessions" / "sess-restart.jsonl"
    line1 = json.dumps({"version": 1, "type": "user", "text": "turn one"}) + "\n"
    line2 = json.dumps({"version": 1, "type": "assistant", "text": "reply one"}) + "\n"
    partial3 = json.dumps({"version": 1, "type": "user", "text": "turn two"})
    _write_jsonl(session_file, [line1, line2, partial3])

    assert _make_tailer(stores, [tmp_path / "sessions"], enabled=True).tail_once().chunks_written == 2
    assert _make_tailer(stores, [tmp_path / "sessions"], enabled=True).tail_once().chunks_written == 0
    assert [c.seq for c in chunk_store.list_chunks(session_id="codex-sess-restart")] == [1, 2]

    line4 = json.dumps({"version": 1, "type": "assistant", "text": "reply two"}) + "\n"
    _write_jsonl(session_file, [line1, line2, partial3 + "\n", line4])
    assert _make_tailer(stores, [tmp_path / "sessions"], enabled=True).tail_once().chunks_written == 2
    assert [c.seq for c in chunk_store.list_chunks(session_id="codex-sess-restart")] == [1, 2, 3, 4]

    bad_tok = "".join(["gh", "p_", "e" * 36])
    with pytest.raises(ValueError, match="node"):
        codex_tailer.CodexSessionTailer(
            roots=[tmp_path], activity_store=activity_store, chunk_store=chunk_store, node="", enabled=True
        )
    with pytest.raises(ValueError, match="sensitive"):
        codex_tailer.CodexSessionTailer(
            roots=[tmp_path], activity_store=activity_store, chunk_store=chunk_store, node=bad_tok, enabled=True
        )
    checkpoint_path.write_text(json.dumps({"schema_version": 999, "files": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        _make_tailer(stores, [tmp_path], enabled=True).tail_once()
