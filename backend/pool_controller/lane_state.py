"""Lane lifecycle states for pool-controller lane files (#4279, C-08)."""

from __future__ import annotations

from enum import StrEnum


class LaneTransitionError(ValueError):
    """Raised when a lane state transition is not permitted."""


class LaneState(StrEnum):
    """Values match backlog-push receipt status literals plus pre-spawn ``pending``."""

    PENDING = "pending"
    ACTIVE = "active"
    RECOVERED = "recovered"
    ROLLED_BACK = "rolled_back"
    TERMINATION_PENDING = "termination_pending"


_S = LaneState
TERMINAL_LANE_STATES: frozenset[LaneState] = frozenset({_S.ROLLED_BACK, _S.TERMINATION_PENDING})

ALLOWED_LANE_TRANSITIONS: dict[LaneState, frozenset[LaneState]] = {
    _S.PENDING: frozenset({_S.ACTIVE, _S.RECOVERED, _S.ROLLED_BACK}),
    _S.ACTIVE: frozenset({_S.ROLLED_BACK, _S.TERMINATION_PENDING}),
    _S.RECOVERED: frozenset({_S.ROLLED_BACK, _S.TERMINATION_PENDING}),
    **dict.fromkeys(TERMINAL_LANE_STATES, frozenset()),
}


def validate_lane_transition(current: LaneState | str, target: LaneState | str) -> LaneState:
    """Return *target* when the transition is allowed; raise otherwise."""
    try:
        cur = current if isinstance(current, _S) else _S(str(current).strip())
        tgt = target if isinstance(target, _S) else _S(str(target).strip())
    except ValueError as exc:
        raise LaneTransitionError(f"invalid lane state: {current!r} -> {target!r}") from exc
    if tgt not in ALLOWED_LANE_TRANSITIONS.get(cur, frozenset()):
        raise LaneTransitionError(f"invalid lane state transition: {cur.value} -> {tgt.value}")
    return tgt
