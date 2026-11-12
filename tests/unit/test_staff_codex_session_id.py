"""Regression tests for Codex thread_id capture (#2773)."""

from __future__ import annotations

import json

import pytest
from staff.adapters import ADAPTERS
from staff.chat_history import extract_session_id
from staff.runner_ops import pump_output
from staff.store import RunRecord, RunStore


@pytest.mark.unit
def test_extract_session_id_from_codex_thread_started_event() -> None:
    event = {
        "kind": "thread.started",
        "text": "",
        "raw": {"type": "thread.started", "thread_id": "019a2f4e-codex-real-thread"},
    }
    assert extract_session_id("codex", event) == "019a2f4e-codex-real-thread"


@pytest.mark.unit
def test_extract_session_id_ignores_session_decoy_in_codex_tool_output() -> None:
    """Tool stdout must not override the thread_id from thread.started."""
    tool_event = {
        "kind": "item.completed",
        "text": "",
        "raw": {
            "type": "item.completed",
            "item": {"type": "tool_output", "text": "Session: Claude\nother noise"},
        },
    }
    assert extract_session_id("codex", tool_event) is None

    started = {
        "kind": "thread.started",
        "text": "",
        "raw": {"type": "thread.started", "thread_id": "thread-019a2f4e"},
    }
    assert extract_session_id("codex", started) == "thread-019a2f4e"


@pytest.mark.unit
def test_pump_output_codex_stream_keeps_thread_id_over_tool_decoy(tmp_path) -> None:
    """Simulate codex exec --json: thread.started then a tool line containing Session: Claude."""
    adapter = ADAPTERS["codex"]
    transcript = tmp_path / "t.log"
    store = RunStore(tmp_path / "runs.sqlite3")
    rec = RunRecord(
        id="run-codex-sess",
        role="worker",
        provider="codex",
        model="gpt-test",
        machine="test",
        repo="UpstreamDrift",
        target_kind="issue",
        target_ref="2773",
        prompt="fix",
        branch="staff/worker-task-abc",
    )
    store.create_run(rec)
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "thread-capture-me"}) + "\n",
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "tool_output", "text": "grep found Session: Claude in file"},
            }
        )
        + "\n",
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 2}}) + "\n",
    ]

    class _Proc:
        stdout = lines

    pump = pump_output(rec, adapter, _Proc(), transcript, store)  # type: ignore[arg-type]
    assert pump.session_id == "thread-capture-me"


@pytest.mark.unit
def test_extract_session_id_does_not_capture_failed_from_tool_output() -> None:
    event = {
        "kind": "item.completed",
        "text": "",
        "raw": {"type": "item.completed", "item": {"type": "tool_output", "text": "command failed with exit 1"}},
    }
    assert extract_session_id("codex", event) is None


@pytest.mark.unit
def test_extract_session_id_codex_agent_message_session_line_still_works() -> None:
    event = {
        "kind": "item.completed",
        "text": "Session: legacy_sess_99",
        "raw": {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Session: legacy_sess_99"},
        },
    }
    assert extract_session_id("codex", event) == "legacy_sess_99"
