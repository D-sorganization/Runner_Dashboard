"""Lane lifecycle states for pool-controller lane files (#4279, C-08; ADR 0012 v2)."""

from __future__ import annotations

from enum import StrEnum


class LaneTransitionError(ValueError):
    """Raised when a lane state transition is not permitted."""


class LaneState(StrEnum):
    """Receipt literals plus pre-spawn ``pending`` and ADR 0012 ``draining`` / ``exited``."""

    PENDING = "pending"
    ACTIVE = "active"
    RECOVERED = "recovered"
    DRAINING = "draining"
    ROLLED_BACK = "rolled_back"
    TERMINATION_PENDING = "termination_pending"
    EXITED = "exited"


_S = LaneState
TERMINAL_LANE_STATES: frozenset[LaneState] = frozenset({_S.ROLLED_BACK, _S.EXITED})

_ACTIVE_LIKE = frozenset(
    {
        _S.ROLLED_BACK,
        _S.TERMINATION_PENDING,
        _S.DRAINING,
        _S.EXITED,
    },
)

ALLOWED_LANE_TRANSITIONS: dict[LaneState, frozenset[LaneState]] = {
    _S.PENDING: frozenset({_S.ACTIVE, _S.RECOVERED, _S.ROLLED_BACK}),
    _S.ACTIVE: _ACTIVE_LIKE,
    _S.RECOVERED: _ACTIVE_LIKE,
    _S.DRAINING: frozenset({_S.ACTIVE, _S.TERMINATION_PENDING, _S.EXITED}),
    _S.TERMINATION_PENDING: frozenset({_S.EXITED}),
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
