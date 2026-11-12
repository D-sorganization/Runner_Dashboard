"""Characterization tests for pool-controller LaneState (#4279, C-08)."""

from __future__ import annotations

import pytest

from pool_controller.lane_state import (
    ALLOWED_LANE_TRANSITIONS,
    TERMINAL_LANE_STATES,
    LaneState,
    LaneTransitionError,
    validate_lane_transition,
)


def test_lane_state_literals_match_backlog_push_receipt_statuses() -> None:
    """Enum values equal today's backlog-push receipt status strings (+ pre-spawn pending)."""
    assert LaneState.ACTIVE == "active"
    assert LaneState.RECOVERED == "recovered"
    assert LaneState.ROLLED_BACK == "rolled_back"
    assert LaneState.TERMINATION_PENDING == "termination_pending"
    assert LaneState.PENDING == "pending"


def test_terminal_lane_states_frozen() -> None:
    assert TERMINAL_LANE_STATES == frozenset(
        {LaneState.ROLLED_BACK, LaneState.TERMINATION_PENDING},
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (LaneState.PENDING, LaneState.ACTIVE),
        (LaneState.PENDING, LaneState.RECOVERED),
        (LaneState.PENDING, LaneState.ROLLED_BACK),
        (LaneState.ACTIVE, LaneState.ROLLED_BACK),
        (LaneState.ACTIVE, LaneState.TERMINATION_PENDING),
        (LaneState.RECOVERED, LaneState.ROLLED_BACK),
        (LaneState.RECOVERED, LaneState.TERMINATION_PENDING),
    ],
)
def test_allowed_lane_transitions(current: LaneState, target: LaneState) -> None:
    assert validate_lane_transition(current, target) == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (LaneState.ACTIVE, LaneState.PENDING),
        (LaneState.ROLLED_BACK, LaneState.ACTIVE),
        (LaneState.TERMINATION_PENDING, LaneState.ACTIVE),
    ],
)
def test_disallowed_lane_transitions(current: LaneState, target: LaneState) -> None:
    with pytest.raises(LaneTransitionError):
        validate_lane_transition(current, target)


def test_transition_table_covers_every_non_terminal_state() -> None:
    for state in LaneState:
        if state in TERMINAL_LANE_STATES:
            assert ALLOWED_LANE_TRANSITIONS.get(state) == frozenset()
        else:
            assert state in ALLOWED_LANE_TRANSITIONS
