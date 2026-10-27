"""Exact evidence comes from marked, persisted run events only."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from staff.adapters import ADAPTERS
from staff.lesson_locator import locate_lesson_evidence, verify_lesson_evidence
from staff.runner_ops import pump_output
from staff.store import RunRecord, RunStore

pytestmark = pytest.mark.unit


@pytest.fixture
def store(tmp_path: Path):
    value = RunStore(tmp_path / "runs.sqlite3")
    value.create_run(RunRecord("run", "role", "claude", None, "machine", "repo", "prompt", "", "test"))
    yield value
    value.close()


def append(store: RunStore, text: str, kind: str = "assistant", marked: bool = True) -> int:
    return store.append_event("run", kind, text, lesson_evidence="assistant_answer" if marked else None)


@pytest.mark.parametrize(
    ("provider", "frame"),
    [
        ("claude", {"type": "assistant", "message": {"content": [{"type": "text", "text": "STAFF_LESSON: exact"}]}}),
        ("codex", {"type": "item.completed", "item": {"type": "agent_message", "text": "STAFF_LESSON: exact"}}),
        ("antigravity", {"event": "result", "result": {"status": "SUCCESS", "response": "STAFF_LESSON: exact"}}),
        ("cursor-agent", {"type": "result", "subtype": "success", "result": "STAFF_LESSON: exact"}),
    ],
)
def test_adapter_pump_store_locator(store: RunStore, tmp_path: Path, provider: str, frame: dict) -> None:
    class Process:
        stdout = io.StringIO(json.dumps(frame) + "\n")

    pump_output(store.get_run("run"), ADAPTERS[provider], Process(), tmp_path / "transcript", store)
    locator = locate_lesson_evidence(store, "run", "repo", "exact")
    assert locator == {
        "version": 1,
        "event_seq": 1,
        "line_index": 0,
        "line_sha256": hashlib.sha256(b"STAFF_LESSON: exact").hexdigest(),
    }
    assert verify_lesson_evidence(store, "run", "repo", "exact", locator)


def test_exact_run_repository_body_and_marker(store: RunStore) -> None:
    append(store, "STAFF_LESSON: exact", marked=False)
    assert locate_lesson_evidence(store, "run", "repo", "exact") is None
    append(store, "intro\n  STAFF_LESSON: exact  ")
    assert locate_lesson_evidence(store, "missing", "repo", "exact") is None
    assert locate_lesson_evidence(store, "run", "Repo", "exact") is None
    assert locate_lesson_evidence(store, "run", "repo", " exact") is None
    locator = locate_lesson_evidence(store, "run", "repo", "exact")
    assert locator["event_seq"] == 2
    assert locator["line_index"] == 1
    assert locator["line_sha256"] == hashlib.sha256(b"STAFF_LESSON: exact").hexdigest()
    store._conn.execute("UPDATE events SET text = 'STAFF_LESSON: changed' WHERE seq = 2")
    assert not verify_lesson_evidence(store, "run", "repo", "exact", locator)


def test_parser_exclusions_fences_across_events_and_lesson_limit(store: RunStore) -> None:
    append(store, '"STAFF_LESSON: quoted"\n> STAFF_LESSON: quoted\n```')
    append(store, "STAFF_LESSON: fenced\n```\nSTAFF_LESSON: first")
    append(store, "STAFF_LESSON: forbidden", kind="tool_output")
    append(store, "STAFF_LESSON: second\nSTAFF_LESSON: third\nSTAFF_LESSON: fourth")
    for body in ("quoted", "fenced", "forbidden", "fourth"):
        assert locate_lesson_evidence(store, "run", "repo", body) is None
    assert locate_lesson_evidence(store, "run", "repo", "first")["line_index"] == 2
    assert locate_lesson_evidence(store, "run", "repo", "third")["line_index"] == 1


def test_pagination_and_scan_limit_fail_closed_even_after_match(store: RunStore) -> None:
    from staff.lesson_locator import MAX_SCAN_EVENTS, PAGE_SIZE

    for _ in range(PAGE_SIZE):
        append(store, "noise", marked=False)
    append(store, "STAFF_LESSON: exact")
    assert locate_lesson_evidence(store, "run", "repo", "exact")["event_seq"] == PAGE_SIZE + 1
    for _ in range(MAX_SCAN_EVENTS - PAGE_SIZE - 1):
        append(store, "noise", marked=False)
    assert locate_lesson_evidence(store, "run", "repo", "exact") is not None
    append(store, "overflow", marked=False)
    with pytest.raises(ValueError, match="scan limit"):
        locate_lesson_evidence(store, "run", "repo", "exact")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", True),
        ("version", 2),
        ("event_seq", True),
        ("event_seq", 0),
        ("line_index", False),
        ("line_index", -1),
        ("line_sha256", "A" * 64),
        ("extra", 1),
    ],
)
def test_locator_is_closed_and_strictly_typed(store: RunStore, field: str, value: object) -> None:
    append(store, "STAFF_LESSON: exact")
    locator = locate_lesson_evidence(store, "run", "repo", "exact")
    locator[field] = value
    assert not verify_lesson_evidence(store, "run", "repo", "exact", locator)


@pytest.mark.parametrize(("field", "value"), [("event_seq", 2), ("line_index", 1), ("line_sha256", "0" * 64)])
def test_valid_shape_still_requires_exact_rederived_evidence(store: RunStore, field: str, value: object) -> None:
    append(store, "STAFF_LESSON: exact")
    locator = locate_lesson_evidence(store, "run", "repo", "exact")
    locator[field] = value
    assert not verify_lesson_evidence(store, "run", "repo", "exact", locator)


@pytest.mark.parametrize("invalid", [None, [], "locator", {}, {"version": 1}])
def test_malformed_locator_fails_closed(store: RunStore, invalid: object) -> None:
    assert not verify_lesson_evidence(store, "run", "repo", "exact", invalid)


@pytest.mark.parametrize("invalid", [None, False, 1, ""])
def test_boundary_rejects_invalid_identity_or_body(store: RunStore, invalid: object) -> None:
    for values in ((invalid, "repo", "exact"), ("run", invalid, "exact"), ("run", "repo", invalid)):
        with pytest.raises(ValueError, match="non-empty strings"):
            locate_lesson_evidence(store, *values)
