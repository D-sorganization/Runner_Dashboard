"""LaneState v2 additions per ADR 0012 (#4279): draining, exited, non-terminal SIGTERM wait."""

from __future__ import annotations

import pytest

from pool_controller.lane_state import (
    TERMINAL_LANE_STATES,
    LaneState,
    LaneTransitionError,
    validate_lane_transition,
)


def test_v2_lane_state_literals() -> None:
    assert LaneState.DRAINING == "draining"
    assert LaneState.EXITED == "exited"


def test_termination_pending_is_not_terminal() -> None:
    assert LaneState.TERMINATION_PENDING not in TERMINAL_LANE_STATES
    assert TERMINAL_LANE_STATES == frozenset({LaneState.ROLLED_BACK, LaneState.EXITED})


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (LaneState.ACTIVE, LaneState.DRAINING),
        (LaneState.ACTIVE, LaneState.EXITED),
        (LaneState.RECOVERED, LaneState.DRAINING),
        (LaneState.RECOVERED, LaneState.EXITED),
        (LaneState.DRAINING, LaneState.ACTIVE),
        (LaneState.DRAINING, LaneState.TERMINATION_PENDING),
        (LaneState.DRAINING, LaneState.EXITED),
        (LaneState.TERMINATION_PENDING, LaneState.EXITED),
    ],
)
def test_v2_allowed_transitions(current: LaneState, target: LaneState) -> None:
    assert validate_lane_transition(current, target) == target


def test_termination_pending_cannot_return_to_active() -> None:
    with pytest.raises(LaneTransitionError):
        validate_lane_transition(LaneState.TERMINATION_PENDING, LaneState.ACTIVE)
