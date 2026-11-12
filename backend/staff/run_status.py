"""Staff run status state machine (#4269 PR 2).

``RunStatus`` values match persisted/API strings exactly. ``transition()`` applies
legal moves with compare-and-set on the stored status column.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from staff.store import RunStore


class RunStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    NEEDS_INPUT = "needs_input"


RUN_STATUSES: tuple[str, ...] = tuple(s.value for s in RunStatus)
ACTIVE_STATUSES: tuple[str, ...] = (
    RunStatus.QUEUED.value,
    RunStatus.PREPARING.value,
    RunStatus.RUNNING.value,
)

# Terminal for the run lifecycle machine (matches claim_guard lease release set).
TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.BLOCKED,
    }
)

LEGAL_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset(
        {RunStatus.PREPARING, RunStatus.CANCELLED, RunStatus.FAILED},
    ),
    RunStatus.PREPARING: frozenset(
        {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.BLOCKED},
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.NEEDS_INPUT,
            RunStatus.BLOCKED,
        },
    ),
    RunStatus.NEEDS_INPUT: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.SUCCEEDED},
    ),
    RunStatus.FAILED: frozenset({RunStatus.SUCCEEDED, RunStatus.BLOCKED}),
    RunStatus.SUCCEEDED: frozenset({RunStatus.FAILED}),
    RunStatus.CANCELLED: frozenset({RunStatus.FAILED}),
    RunStatus.BLOCKED: frozenset(),
}


class IllegalRunStatusTransitionError(ValueError):
    """Raised when ``to`` is not allowed from ``from_`` in ``LEGAL_TRANSITIONS``."""


class RunStatusRaceError(RuntimeError):
    """Raised when compare-and-set loses because the row status differed from ``from_``."""

    def __init__(self, run_id: str, expected: RunStatus, actual: str | None) -> None:
        super().__init__(f"run {run_id}: expected status {expected.value!r}, found {actual!r}")
        self.run_id = run_id
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True)
class TransitionResult:
    ok: bool
    run_id: str
    from_status: RunStatus
    to_status: RunStatus
    actual_status: str | None = None


def _coerce_status(value: RunStatus | str) -> RunStatus:
    if isinstance(value, RunStatus):
        return value
    return RunStatus(value)


def is_legal_transition(from_status: RunStatus | str, to_status: RunStatus | str) -> bool:
    current = _coerce_status(from_status)
    target = _coerce_status(to_status)
    if current == target:
        return False
    allowed = LEGAL_TRANSITIONS.get(current, frozenset())
    return target in allowed


def transition(
    store: RunStore,
    run_id: str,
    from_status: RunStatus | str,
    to_status: RunStatus | str,
    *,
    raise_on_race: bool = True,
    **fields: Any,
) -> TransitionResult:
    """Compare-and-set status transition plus optional non-status column updates.

    Pre: ``from_status`` and ``to_status`` are legal unless identical (no-op success).
    Post: row updated when CAS wins; raises or returns ``ok=False`` on a lost race.
    """
    expected = _coerce_status(from_status)
    target = _coerce_status(to_status)
    if expected == target:
        return TransitionResult(ok=True, run_id=run_id, from_status=expected, to_status=target)

    if not is_legal_transition(expected, target):
        raise IllegalRunStatusTransitionError(
            f"illegal run status transition {run_id}: {expected.value!r} → {target.value!r}"
        )

    if "status" in fields:
        raise ValueError("pass status via to_status, not in fields")

    try:
        store.update_run(
            run_id,
            status=target.value,
            _expected_status=expected.value,
            **fields,
        )
    except RunStatusRaceError:
        pass
    else:
        return TransitionResult(ok=True, run_id=run_id, from_status=expected, to_status=target)

    actual = store.get_run(run_id)
    actual_status = None if actual is None else actual.status
    result = TransitionResult(
        ok=False,
        run_id=run_id,
        from_status=expected,
        to_status=target,
        actual_status=actual_status,
    )
    if raise_on_race:
        raise RunStatusRaceError(run_id, expected, actual_status)
    return result
