"""Pure-function lifecycle state machine for Code Requests (CR-2, issue #1282)."""

from __future__ import annotations

from datetime import UTC, datetime

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
    CodeRequestState.PLANNING: frozenset({CodeRequestState.PLANNED}),
    CodeRequestState.PLANNED: frozenset({CodeRequestState.EXECUTING}),
    CodeRequestState.EXECUTING: frozenset({CodeRequestState.DONE, CodeRequestState.FAILED}),
    CodeRequestState.DONE: frozenset(),
    CodeRequestState.FAILED: frozenset(),
    CodeRequestState.DEFERRED: frozenset(),
    CodeRequestState.DECLINED: frozenset(),
    CodeRequestState.CANCELLED: frozenset(),
}

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
    now: str | None = None,
) -> CodeRequest:
    """Execute a pure state machine transition on a CodeRequest.

    Appends an audit event to the request's audit trail, updates its state and
    updated_at timestamp, and returns a new CodeRequest instance.

    Raises InvalidTransitionError if the transition is illegal.
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
