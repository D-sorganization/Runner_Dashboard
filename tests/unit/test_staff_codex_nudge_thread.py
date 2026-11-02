"""Codex nudge thread continuity (#3591)."""

from __future__ import annotations

import json

import pytest
from staff.adapters import ADAPTERS
from staff.runner_stream import pump_nudge_output
from staff.store import RunRecord, RunStore


@pytest.mark.unit
def test_pump_nudge_accepts_staff_result_on_matching_thread(tmp_path) -> None:
    adapter = ADAPTERS["codex"]
    store = RunStore(tmp_path / "runs.sqlite3")
    rec = RunRecord(
        id="run-nudge-ok",
        role="worker",
        provider="codex",
        model="gpt-test",
        machine="test",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="3591",
        prompt="fix",
    )
    store.create_run(rec)
    transcript = tmp_path / "t.log"
    expected = "thread-original"
    lines = [
        json.dumps({"type": "thread.started", "thread_id": expected}) + "\n",
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "STAFF_RESULT: done"},
            }
        )
        + "\n",
    ]

    class _Proc:
        stdout = lines

    pump = pump_nudge_output(
        rec,
        adapter,
        _Proc(),  # type: ignore[arg-type]
        transcript,
        store,
        expected_session_id=expected,
    )
    assert pump.result_line == "STAFF_RESULT: done"


@pytest.mark.unit
def test_pump_nudge_rejects_staff_result_from_foreign_thread(tmp_path) -> None:
    adapter = ADAPTERS["codex"]
    store = RunStore(tmp_path / "runs.sqlite3")
    rec = RunRecord(
        id="run-nudge-bad",
        role="worker",
        provider="codex",
        model="gpt-test",
        machine="test",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="3591",
        prompt="fix",
    )
    store.create_run(rec)
    transcript = tmp_path / "t.log"
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "foreign-thread"}) + "\n",
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "STAFF_RESULT: hijacked"},
            }
        )
        + "\n",
    ]

    class _Proc:
        stdout = lines

    pump = pump_nudge_output(
        rec,
        adapter,
        _Proc(),  # type: ignore[arg-type]
        transcript,
        store,
        expected_session_id="thread-original",
    )
    assert pump.result_line == ""
    events = [ev.get("text") or "" for ev in store.events_after(rec.id, 0, 50)]
    assert any("observed thread" in text.lower() or "nudge thread" in text.lower() for text in events)
