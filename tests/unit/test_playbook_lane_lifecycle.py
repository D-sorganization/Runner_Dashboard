"""Unit tests for playbook lane fetch/ack/completion lifecycle and the swarm sweep (#3489)."""

from __future__ import annotations

import pytest
from staff.playbooks.assignments import (
    PlaybookLaneError,
    acknowledge_assignment,
    clear_assignments,
    complete_assignment,
    create_assignment,
    fetch_assignment,
    sweep_assignments,
)
from staff.playbooks.schema import PlaybookDefinition, PlaybookParameter, PlaybookValidationError

_PB = PlaybookDefinition(
    schema_version=1,
    playbook_id="overnight-codex-swarm",
    version="1.2.0",
    title="Overnight Codex Swarm Playbook",
    summary="Run bounded overnight swarm lanes.",
    manual_anchors=("claim-before-touching",),
    directive_refs=("fleet-standards",),
    role_refs=("barb", "codex-worker"),
    claim_endpoint="POST /claims",
    claim_release_endpoint="POST /claims/release",
    parameters=(PlaybookParameter("lane_id", "string", required=True),),
    steps=("Follow manual#claim-before-touching.",),
    sha256="deadbeef" * 8,
    pin="overnight-codex-swarm@1.2.0+deadbeefcafe",
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    clear_assignments()
    yield
    clear_assignments()


def _new_assignment(now: float = 1_000.0) -> dict[str, object]:
    return create_assignment(_PB, "barb", {"lane_id": "claude-a"}, now=now)


def test_create_assignment_records_created_and_last_seen() -> None:
    record = _new_assignment(now=1_000.0)
    assert record["lane_id"] is None
    assert record["created_at"] == record["last_seen_at"] == 1_000.0
    assert record["events"] == []


def test_fetch_assignment_returns_record_and_unknown_id_raises() -> None:
    record = _new_assignment()
    assert fetch_assignment(record["assignment_id"])["state"] == "ASSIGNED"
    with pytest.raises(KeyError):
        fetch_assignment(999999)


def test_fetch_by_assigned_lane_bumps_last_seen() -> None:
    record = _new_assignment(now=1_000.0)
    acknowledge_assignment(record["assignment_id"], "claude-a", _PB.pin, now=1_010.0)
    fetched = fetch_assignment(record["assignment_id"], lane_id="claude-a", now=1_020.0)
    assert fetched["last_seen_at"] == 1_020.0


def test_fetch_by_a_different_lane_fails_closed_without_bumping() -> None:
    record = _new_assignment(now=1_000.0)
    acknowledge_assignment(record["assignment_id"], "claude-a", _PB.pin, now=1_010.0)
    with pytest.raises(PlaybookLaneError):
        fetch_assignment(record["assignment_id"], lane_id="codex-d", now=1_020.0)
    assert fetch_assignment(record["assignment_id"])["last_seen_at"] == 1_010.0


def test_acknowledge_stamps_version_hash_lane_and_event() -> None:
    record = _new_assignment(now=1_000.0)
    acked = acknowledge_assignment(record["assignment_id"], "claude-a", _PB.pin, now=1_005.0)
    assert acked["state"] == "ACKNOWLEDGED"
    assert acked["lane_id"] == "claude-a"
    assert acked["last_seen_at"] == 1_005.0
    assert acked["events"] == [{"state": "ACKNOWLEDGED", "at": 1_005.0, "lane_id": "claude-a", "version_hash": _PB.pin}]


def test_acknowledge_with_stale_version_hash_fails_closed() -> None:
    record = _new_assignment()
    with pytest.raises(PlaybookValidationError, match="version"):
        acknowledge_assignment(record["assignment_id"], "claude-a", "stale-pin", now=1_005.0)
    assert fetch_assignment(record["assignment_id"])["state"] == "ASSIGNED"


def test_acknowledge_twice_is_an_invalid_transition() -> None:
    record = _new_assignment()
    acknowledge_assignment(record["assignment_id"], "claude-a", _PB.pin, now=1_005.0)
    with pytest.raises(PlaybookValidationError, match="transition"):
        acknowledge_assignment(record["assignment_id"], "claude-a", _PB.pin, now=1_006.0)


def test_complete_stamps_version_hash_and_outcome() -> None:
    record = _new_assignment(now=1_000.0)
    acknowledge_assignment(record["assignment_id"], "claude-a", _PB.pin, now=1_005.0)
    done = complete_assignment(record["assignment_id"], "claude-a", _PB.pin, "COMPLETED", now=1_050.0)
    assert done["state"] == "COMPLETED"
    assert done["events"][-1] == {"state": "COMPLETED", "at": 1_050.0, "lane_id": "claude-a", "version_hash": _PB.pin}


def test_complete_by_a_different_lane_fails_closed() -> None:
    record = _new_assignment()
    acknowledge_assignment(record["assignment_id"], "claude-a", _PB.pin, now=1_005.0)
    with pytest.raises(PlaybookLaneError):
        complete_assignment(record["assignment_id"], "codex-d", _PB.pin, "COMPLETED", now=1_050.0)
    assert fetch_assignment(record["assignment_id"])["state"] == "ACKNOWLEDGED"


def test_complete_before_acknowledge_is_an_invalid_transition() -> None:
    record = _new_assignment()
    with pytest.raises(PlaybookValidationError, match="transition"):
        complete_assignment(record["assignment_id"], "claude-a", _PB.pin, "COMPLETED", now=1_050.0)


def test_sweep_marks_silent_acknowledged_lane_as_timed_out() -> None:
    record = _new_assignment(now=1_000.0)
    acknowledge_assignment(record["assignment_id"], "claude-a", _PB.pin, now=1_005.0)
    result = sweep_assignments(now=1_500.0, silent_after_seconds=60.0, abandon_after_seconds=3600.0)
    assert result == {"timed_out": 1, "orphaned": 0}
    swept = fetch_assignment(record["assignment_id"])
    assert swept["state"] == "TIMED_OUT" and swept["events"][-1]["state"] == "TIMED_OUT"


def test_sweep_marks_never_acknowledged_assignment_as_orphaned() -> None:
    record = _new_assignment(now=1_000.0)
    result = sweep_assignments(now=1_500.0, silent_after_seconds=3600.0, abandon_after_seconds=60.0)
    assert result == {"timed_out": 0, "orphaned": 1}
    assert fetch_assignment(record["assignment_id"])["state"] == "ORPHANED"


def test_sweep_leaves_fresh_assignments_alone() -> None:
    record = _new_assignment(now=1_000.0)
    acknowledge_assignment(record["assignment_id"], "claude-a", _PB.pin, now=1_005.0)
    result = sweep_assignments(now=1_010.0, silent_after_seconds=60.0, abandon_after_seconds=60.0)
    assert result == {"timed_out": 0, "orphaned": 0}
    assert fetch_assignment(record["assignment_id"])["state"] == "ACKNOWLEDGED"


def test_sweep_classifies_a_mixed_batch_of_lanes_in_one_call() -> None:
    silent = _new_assignment(now=1_000.0)
    acknowledge_assignment(silent["assignment_id"], "claude-a", _PB.pin, now=1_005.0)
    abandoned = _new_assignment(now=1_000.0)
    fresh = _new_assignment(now=1_490.0)
    acknowledge_assignment(fresh["assignment_id"], "claude-f", _PB.pin, now=1_495.0)

    result = sweep_assignments(now=1_500.0, silent_after_seconds=60.0, abandon_after_seconds=60.0)

    assert result == {"timed_out": 1, "orphaned": 1}
    assert fetch_assignment(silent["assignment_id"])["state"] == "TIMED_OUT"
    assert fetch_assignment(abandoned["assignment_id"])["state"] == "ORPHANED"
    assert fetch_assignment(fresh["assignment_id"])["state"] == "ACKNOWLEDGED"
