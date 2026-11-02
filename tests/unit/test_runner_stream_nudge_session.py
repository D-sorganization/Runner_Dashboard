"""Codex result-nudge thread continuity (#3591)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from staff.runner_stream import pump_nudge_output
from staff.store import RunRecord, RunStore


class CodexLineAdapter:
    provider_id = "codex"

    def parse_line(self, line: str) -> dict[str, Any]:
        line = line.strip()
        if line.startswith("{"):
            return {"kind": "json", "raw": json.loads(line)}
        return {"kind": "text", "text": line}


@pytest.fixture
def rec() -> RunRecord:
    return RunRecord(
        id="run-nudge-3591",
        role="worker",
        provider="codex",
        model="gpt-test",
        machine="testbox",
        repo="D-sorganization/Runner_Dashboard",
        target_kind="issue",
        target_ref="3591",
        prompt="nudge",
    )


def test_nudge_accepts_staff_result_when_session_matches(tmp_path, rec: RunRecord) -> None:
    adapter = CodexLineAdapter()
    store = MagicMock(spec=RunStore)
    proc = MagicMock()
    proc.stdout = [
        json.dumps({"type": "thread.started", "thread_id": "thread-original"}) + "\n",
        "STAFF_RESULT: ok\n",
    ]
    pump = pump_nudge_output(
        rec,
        adapter,
        proc,
        tmp_path / "t.log",
        store,
        expected_session_id="thread-original",
    )
    assert pump.result_line == "STAFF_RESULT: ok"
    assert pump.usage == {}


def test_nudge_rejects_staff_result_on_session_mismatch(tmp_path, rec: RunRecord) -> None:
    adapter = CodexLineAdapter()
    store = MagicMock(spec=RunStore)
    proc = MagicMock()
    proc.stdout = [
        json.dumps({"type": "thread.started", "thread_id": "thread-other"}) + "\n",
        "STAFF_RESULT: hijacked\n",
    ]
    pump = pump_nudge_output(
        rec,
        adapter,
        proc,
        tmp_path / "t.log",
        store,
        expected_session_id="thread-original",
    )
    assert pump.result_line == ""
    mismatch_calls = [
        c
        for c in store.append_event.call_args_list
        if c.args[1] == "nudge" and "rejected STAFF_RESULT" in c.args[2]
    ]
    assert mismatch_calls


def test_nudge_rejects_staff_result_without_announced_session(tmp_path, rec: RunRecord) -> None:
    adapter = CodexLineAdapter()
    store = MagicMock(spec=RunStore)
    proc = MagicMock()
    proc.stdout = ["STAFF_RESULT: orphan\n"]
    pump = pump_nudge_output(
        rec,
        adapter,
        proc,
        tmp_path / "t.log",
        store,
        expected_session_id="thread-original",
    )
    assert pump.result_line == ""
    reject_calls = [c for c in store.append_event.call_args_list if "announced no session" in c.args[2]]
    assert reject_calls
