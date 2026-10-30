"""A result nudge must answer in the Codex thread it was asked to resume (#3591)."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from staff import runner_ops, runner_stream
from staff.adapters import ADAPTERS


class EventStore:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    def append_event(self, run_id: str, kind: str, text: str, **kwargs: object) -> None:
        self.events.append((run_id, kind, text))


def nudge_process(thread: str | None) -> SimpleNamespace:
    events = []
    if thread is not None:
        events.append({"type": "thread.started", "thread_id": thread})
    events.extend(
        [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "STAFF_RESULT: verified"}},
            {"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 2}},
        ]
    )
    return SimpleNamespace(stdout=[json.dumps(event) + "\n" for event in events], wait=lambda **kwargs: 0)


@pytest.mark.unit
@pytest.mark.parametrize("observed", ["original-thread", "different-thread", None])
def test_nudge_requires_original_codex_thread(tmp_path, observed: str | None) -> None:
    store = EventStore()
    transcript = tmp_path / "nudge.log"
    result = runner_stream.pump_nudge_output(
        SimpleNamespace(id="run-test", provider="codex"),
        ADAPTERS["codex"],
        nudge_process(observed),
        transcript,
        store,
        expected_session_id="original-thread",
    )
    assert result.session_id == observed
    assert result.usage == {"input_tokens": 4, "output_tokens": 2}
    assert result.result_line == ("STAFF_RESULT: verified" if observed == "original-thread" else "")
    assert "STAFF_RESULT: verified" in transcript.read_text()
    if observed != "original-thread":
        assert any(kind == "nudge" and "thread" in text for _, kind, text in store.events)


@pytest.mark.unit
@pytest.mark.parametrize("observed", ["original-thread", "different-thread"])
def test_execute_nudge_checks_the_requested_session(tmp_path, monkeypatch, observed: str) -> None:
    monkeypatch.setattr(runner_ops, "resolve_launch_paths", lambda *args: {"gitdir": str(tmp_path)})
    monkeypatch.setattr(runner_stream.shutil, "which", lambda executable: executable)
    monkeypatch.setattr(runner_stream.subprocess, "Popen", lambda *args, **kwargs: nudge_process(observed))
    monkeypatch.setattr(
        runner_stream,
        "StaffWatchdog",
        lambda **kwargs: SimpleNamespace(start=lambda: None, stop=lambda: None, record_output=lambda: None),
    )
    result, usage, rc = runner_stream.execute_retry_nudge(
        rec=SimpleNamespace(id="run-test", provider="codex"),
        adapter=ADAPTERS["codex"],
        plan=SimpleNamespace(model="gpt-6-luna"),
        role=None,
        workdir=tmp_path,
        env={},
        session_id="original-thread",
        transcript=tmp_path / "nudge.log",
        store=EventStore(),
        wall_clock_timeout=30,
        idle_timeout=10,
        lock=threading.Lock(),
        procs={},
        cancel_flags=set(),
    )
    assert result == ("STAFF_RESULT: verified" if observed == "original-thread" else "")
    assert usage == {"input_tokens": 4, "output_tokens": 2}
    assert rc == 0
