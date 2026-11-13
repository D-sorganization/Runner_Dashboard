from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from staff.adapters import ADAPTERS
from staff.runner_ops import pump_output
from staff.store import RunRecord, RunStore


def _claude(kind: str, *, content_kind: str = "text") -> dict[str, Any]:
    return {"type": kind, "message": {"content": [{"type": content_kind, "text": "STAFF_LESSON: stable evidence"}]}}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("provider", "frame"),
    [
        ("claude", _claude("assistant")),
        ("codex", {"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}}),
        ("antigravity", {"event": "result", "result": {"status": "SUCCESS", "response": "answer"}}),
        ("cursor-agent", {"type": "result", "subtype": "success", "result": "answer"}),
    ],
)
def test_fixture_backed_answer_frames_receive_lesson_evidence(provider: str, frame: dict[str, Any]) -> None:
    parsed = ADAPTERS[provider].parse_line(json.dumps(frame))
    assert parsed["lesson_evidence"] == "assistant_answer"
    assert parsed["text"]
    assert parsed["kind"] == frame.get("type", frame.get("event"))


@pytest.mark.unit
def test_answer_marker_requires_text_to_match_the_approved_provider_field() -> None:
    claude_shadow = _claude("assistant")
    claude_shadow["text"] = "STAFF_LESSON: unrelated top-level text"
    agy_shadow = {
        "event": "result",
        "result": {"status": "SUCCESS", "response": "answer", "text": "unrelated nested text"},
    }
    codex_answer = {
        "type": "item.completed",
        "text": "unrelated top-level text",
        "item": {"type": "agent_message", "text": "answer"},
    }
    cursor_answer = {"type": "result", "subtype": "success", "result": "answer", "text": "unrelated top-level text"}

    assert ADAPTERS["claude"].parse_line(json.dumps(claude_shadow)).get("lesson_evidence") is None
    assert ADAPTERS["antigravity"].parse_line(json.dumps(agy_shadow)).get("lesson_evidence") is None
    assert ADAPTERS["codex"].parse_line(json.dumps(codex_answer))["lesson_evidence"] == "assistant_answer"
    assert ADAPTERS["cursor-agent"].parse_line(json.dumps(cursor_answer))["lesson_evidence"] == "assistant_answer"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("provider", "frame"),
    [
        ("claude", {**_claude("assistant"), "is_error": True}),
        (
            "claude",
            {**_claude("assistant"), "message": {"role": "user", "content": [{"type": "text", "text": "answer"}]}},
        ),
        ("claude", {**_claude("assistant"), "status": "failed"}),
        ("claude", _claude("assistant", content_kind="tool_use")),
        ("claude", _claude("user")),
        ("claude", {"type": "result", "result": "answer"}),
        ("codex", {"type": "item.completed", "item": {"type": "command_execution", "aggregated_output": "answer"}}),
        ("codex", {"type": "item.updated", "item": {"type": "agent_message", "text": "partial"}}),
        ("antigravity", {"event": "result", "result": {"status": "ERROR", "response": "answer"}}),
        ("cursor-agent", {"type": "result", "subtype": "error", "result": "answer"}),
        ("claude", {"type": "new_provider_event", "text": "answer"}),
        ("gemini", {"type": "result", "response": "answer"}),
    ],
)
def test_unsupported_or_nonanswer_frames_have_no_lesson_evidence(provider: str, frame: dict[str, Any]) -> None:
    parsed = ADAPTERS[provider].parse_line(json.dumps(frame))
    assert parsed.get("lesson_evidence") is None


@pytest.mark.unit
def test_malformed_and_plain_fallback_output_never_receives_lesson_evidence() -> None:
    assert ADAPTERS["claude"].parse_line("not json").get("lesson_evidence") is None
    assert ADAPTERS["claude"].parse_line("{broken").get("lesson_evidence") is None
    assert (
        ADAPTERS["claude"].parse_line(json.dumps(_claude("assistant", content_kind="text"))).get("lesson_evidence")
        == "assistant_answer"
    )


@pytest.mark.unit
def test_event_store_migrates_old_events_and_keeps_marker_out_of_default_api_shape(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, run_id TEXT, seq INTEGER, ts TEXT, kind TEXT, text TEXT)"
        )
        conn.execute("INSERT INTO events VALUES (1, 'run-1', 1, '2026-01-01T00:00:00Z', 'assistant', 'old answer')")
    store = RunStore(path)
    assert store.events_after("run-1")[0] == {
        "seq": 1,
        "ts": "2026-01-01T00:00:00Z",
        "kind": "assistant",
        "text": "old answer",
    }
    assert store.events_after("run-1", with_evidence=True)[0]["lesson_evidence"] is None
    store.close()


@pytest.mark.unit
def test_store_persists_only_closed_marker_and_drops_it_when_text_changes(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "events.sqlite3")
    with pytest.raises(ValueError, match="lesson evidence"):
        store.append_event("run-1", "assistant", "answer", lesson_evidence="caller_claim")
    store.append_event("run-1", "assistant", "answer", lesson_evidence="assistant_answer")
    assert store.events_after("run-1", with_evidence=True)[0]["lesson_evidence"] == "assistant_answer"
    exact_limit_seq = store.append_event("run-1", "assistant", "x" * 4000, lesson_evidence="assistant_answer")
    exact_limit = store.events_after("run-1", exact_limit_seq - 1, with_evidence=True)[0]
    assert len(exact_limit["text"]) == 4000
    assert exact_limit["lesson_evidence"] == "assistant_answer"
    store.append_event("run-1", "assistant", "x" * 4001, lesson_evidence="assistant_answer")
    over_limit = store.events_after("run-1", exact_limit_seq, with_evidence=True)[0]
    assert len(over_limit["text"]) == 4000
    assert over_limit["lesson_evidence"] is None
    changed_text_seq = store.append_event(
        "run-1", "assistant", "STAFF_LESSON: internal address 10.1.2.3", lesson_evidence="assistant_answer"
    )
    changed_text = store.events_after("run-1", changed_text_seq - 1, with_evidence=True)[0]
    assert changed_text["text"] == "STAFF_LESSON: internal address [REDACTED_IP]"
    assert changed_text["lesson_evidence"] is None
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO events (run_id, seq, ts, kind, text, lesson_evidence) "
            "VALUES ('run-1', 99, 'now', 'assistant', 'answer', 'forged')"
        )
    store.close()


@pytest.mark.unit
def test_pump_persists_provider_classification_without_changing_event_text(tmp_path: Path) -> None:
    class Process:
        stdout = io.StringIO(json.dumps(_claude("assistant")) + "\n" + json.dumps(_claude("error")) + "\n")

    store = RunStore(tmp_path / "pump.sqlite3")
    rec = RunRecord(
        id="run-pump",
        role="r",
        provider="claude",
        model=None,
        machine="m",
        repo="repo",
        target_kind="prompt",
        target_ref="",
        prompt="test",
    )
    result = pump_output(rec, ADAPTERS["claude"], Process(), tmp_path / "transcript.log", store)
    assert result[0] == {}
    events = store.events_after(rec.id, with_evidence=True)
    assert [(row["kind"], row["text"], row["lesson_evidence"]) for row in events] == [
        ("assistant", "STAFF_LESSON: stable evidence", "assistant_answer"),
        ("error", "STAFF_LESSON: stable evidence", None),
    ]
    store.close()
