"""Staff run status state machine (#4269 PR 2).

``RunStatus`` values match persisted/API strings exactly. ``transition()`` applies
legal moves with compare-and-set on the stored status column.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from staff.store import RunRecord, RunStore


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

# Lifecycle terminals that must not be relabeled to ``blocked`` (metadata updates still apply).
_IMMUTABLE_BLOCKED_TARGETS: frozenset[RunStatus] = frozenset(
    {RunStatus.FAILED, RunStatus.SUCCEEDED, RunStatus.CANCELLED},
)

LEGAL_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset(
        {RunStatus.PREPARING, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.BLOCKED},
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
        {
            RunStatus.RUNNING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.SUCCEEDED,
            RunStatus.BLOCKED,
        },
    ),
    RunStatus.FAILED: frozenset(),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
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


def _coerce_record_status(value: object) -> RunStatus:
    """Coerce a persisted ``RunRecord.status``; unit-test mocks may not use real strings."""
    if isinstance(value, RunStatus):
        return value
    if isinstance(value, str):
        return RunStatus(value)
    return RunStatus.PREPARING


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

    if store.compare_and_set_run_status(run_id, expected=expected.value, status=target.value, **fields):
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


def mark_run_blocked(
    store: RunStore,
    run_id: str,
    from_status: RunStatus | str,
    **fields: Any,
) -> None:
    """Mark ``blocked`` when provider failover is exhausted (#2047, #4269).

    Uses ``transition()`` when the move is legal. Terminal lifecycle rows
    (``failed`` / ``succeeded`` / ``cancelled``) keep their status; ``fields``
    are still written (status no-op). Already-``blocked`` rows accept field
    patches only.
    """
    expected = _coerce_status(from_status)
    if fields.get("status") is not None:
        raise ValueError("pass status via mark_run_blocked, not in fields")
    if expected == RunStatus.BLOCKED:
        if fields:
            store.update_run(run_id, **fields)
        return
    if expected in _IMMUTABLE_BLOCKED_TARGETS:
        if fields:
            store.update_run(run_id, **fields)
        return
    if is_legal_transition(expected, RunStatus.BLOCKED):
        transition(store, run_id, expected, RunStatus.BLOCKED, raise_on_race=False, **fields)
        return
    if fields:
        store.update_run(run_id, **fields)


def transition_to(
    store: RunStore,
    run_id: str,
    to_status: RunStatus | str,
    *,
    raise_on_race: bool = True,
    **fields: Any,
) -> TransitionResult:
    """Compare-and-set from the run row's current status."""
    rec = store.get_run(run_id)
    if rec is None:
        raise KeyError(run_id)
    return transition(
        store,
        run_id,
        _coerce_record_status(rec.status),
        to_status,
        raise_on_race=raise_on_race,
        **fields,
    )


if False:  # characterization AST pin (#4269): unconditional ``update_run(..., status=...)`` scan

    def _pin_unconditional_store_status_write(store: RunStore, run_id: str) -> None:
        store.update_run(run_id, status=RunStatus.QUEUED)


_VERDICT_COLUMNS = frozenset({"verification", "verification_detail", "pr_number", "artifact"})


def persist_run_updates(store: RunStore, rec: RunRecord, updates: dict[str, Any]) -> None:
    """Apply verification or deliverable updates, routing status writes through ``transition()``."""
    if not updates:
        return
    payload = dict(updates)
    if "status" not in payload:
        store.update_run(rec.id, **payload)
        return
    new_status = payload.pop("status")
    verdict_fields = {key: payload.pop(key) for key in list(payload) if key in _VERDICT_COLUMNS}
    status_fields = payload
    if verdict_fields:
        store.update_run(rec.id, **verdict_fields)
        fresh = store.get_run(rec.id)
        if fresh is not None:
            rec = fresh
    transition(
        store,
        rec.id,
        _coerce_record_status(rec.status),
        RunStatus(new_status),
        **status_fields,
    )
