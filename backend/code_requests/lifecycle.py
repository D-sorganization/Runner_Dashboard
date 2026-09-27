"""Pure-function lifecycle state machine for Code Requests (CR-2, issue #1282)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from code_requests.model import CodeRequest, CodeRequestAuditEvent, CodeRequestState


class InvalidTransitionError(ValueError):
    """Raised when an illegal lifecycle transition is attempted."""


LEGAL_TRANSITIONS: dict[CodeRequestState, frozenset[CodeRequestState]] = {
    CodeRequestState.DRAFT: frozenset({CodeRequestState.TRIAGE}),
    CodeRequestState.TRIAGE: frozenset({CodeRequestState.BOARD_REVIEW, CodeRequestState.PLANNING}),
    CodeRequestState.BOARD_REVIEW: frozenset(
        {
            CodeRequestState.PLANNING,
            CodeRequestState.DEFERRED,
            CodeRequestState.DECLINED,
        }
    ),
    # PLANNING -> FAILED: the planner output was rejected after its retries (CR-4, #1285).
    CodeRequestState.PLANNING: frozenset({CodeRequestState.PLANNED, CodeRequestState.FAILED}),
    CodeRequestState.PLANNED: frozenset({CodeRequestState.EXECUTING}),
    CodeRequestState.EXECUTING: frozenset({CodeRequestState.DONE, CodeRequestState.FAILED}),
    CodeRequestState.DONE: frozenset(),
    CodeRequestState.FAILED: frozenset(),
    CodeRequestState.DEFERRED: frozenset(),
    CodeRequestState.DECLINED: frozenset(),
    CodeRequestState.CANCELLED: frozenset(),
}


class TransitionGate(StrEnum):
    """Evidence a caller must name to enter a gated state (#1605).

    Only the service that checked the evidence passes its gate: the plan service
    after it files the plan (and after owner approval when the profile requires
    it), and the acceptance check once every child PR is verified.
    """

    PLAN_FILED = "plan_filed"
    ACCEPTANCE = "acceptance"


GATED_TARGETS: dict[CodeRequestState, TransitionGate] = {
    CodeRequestState.PLANNED: TransitionGate.PLAN_FILED,
    CodeRequestState.DONE: TransitionGate.ACCEPTANCE,
}


def required_gate(to_state: CodeRequestState) -> TransitionGate | None:
    """Return the gate a transition into ``to_state`` needs, or None if it is ungated."""
    return GATED_TARGETS.get(to_state)


PRE_PLANNING_STATES: frozenset[CodeRequestState] = frozenset(
    {
        CodeRequestState.DRAFT,
        CodeRequestState.TRIAGE,
        CodeRequestState.BOARD_REVIEW,
    }
)


def is_legal_transition(
    from_state: CodeRequestState,
    to_state: CodeRequestState,
    *,
    is_operator_override: bool = False,
) -> bool:
    """Return True if transitioning from_state -> to_state is valid under the given mode."""
    if from_state == to_state:
        return False

    if is_operator_override:
        if to_state == CodeRequestState.CANCELLED:
            return True
        if from_state in PRE_PLANNING_STATES and to_state in (
            CodeRequestState.PLANNING,
            CodeRequestState.BOARD_REVIEW,
        ):
            return True

    return to_state in LEGAL_TRANSITIONS.get(from_state, frozenset())


def transition(
    request: CodeRequest,
    to_state: CodeRequestState | str,
    *,
    actor: str,
    reason: str,
    is_operator_override: bool = False,
    gate: TransitionGate | None = None,
    now: str | None = None,
) -> CodeRequest:
    """Execute a pure state machine transition on a CodeRequest.

    Appends an audit event to the request's audit trail, updates its state and
    updated_at timestamp, and returns a new CodeRequest instance.

    Preconditions: the move is legal (``is_legal_transition``), and a gated target
    (``GATED_TARGETS``) is entered only with its ``gate`` or an operator override,
    which the audit event records.
    Raises InvalidTransitionError if either precondition fails.
    """
    if isinstance(to_state, str):
        try:
            target_state = CodeRequestState(to_state)
        except ValueError as err:
            raise InvalidTransitionError(f"Unknown target state: {to_state!r}") from err
    else:
        target_state = to_state

    from_state = request.state

    if not is_legal_transition(from_state, target_state, is_operator_override=is_operator_override):
        override_suffix = " (with operator override)" if is_operator_override else ""
        raise InvalidTransitionError(
            f"Cannot transition Code Request {request.id} from {from_state.value!r} to "
            f"{target_state.value!r}{override_suffix}"
        )

    needed = required_gate(target_state)
    if needed is not None and gate is not needed and not is_operator_override:
        raise InvalidTransitionError(
            f"Cannot move Code Request {request.id} to {target_state.value!r} without the "
            f"{needed.value!r} gate; use its service or an operator override"
        )

    timestamp = now or datetime.now(UTC).isoformat()
    audit_event = CodeRequestAuditEvent(
        actor=actor,
        from_state=from_state,
        to_state=target_state,
        reason=reason,
        timestamp=timestamp,
        override=is_operator_override,
    )

    updated = request.model_copy(deep=True)
    updated.state = target_state
    updated.updated_at = timestamp
    updated.audit_trail.append(audit_event)

    return updated
