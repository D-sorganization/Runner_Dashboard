"""Property-style and unit tests for Code Request lifecycle state machine (CR-2, #1282)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from code_requests.lifecycle import (  # noqa: E402
    InvalidTransitionError,
    is_legal_transition,
    transition,
)
from code_requests.model import (  # noqa: E402
    BoardRoute,
    CodeRequest,
    CodeRequestState,
    Requester,
    RequesterKind,
)


def _sample_request(state: CodeRequestState) -> CodeRequest:
    return CodeRequest(
        id="cr-test-1",
        repository="Runner_Dashboard",
        state=state,
        prompt="Sample prompt for testing",
        requester=Requester(id="tester", kind=RequesterKind.HUMAN),
        board_route=BoardRoute.AUTO,
        created_at="2026-09-25T12:00:00Z",
        updated_at="2026-09-25T12:00:00Z",
    )


@given(
    from_state=st.sampled_from(list(CodeRequestState)),
    to_state=st.sampled_from(list(CodeRequestState)),
    override=st.booleans(),
)
def test_lifecycle_property_all_transitions(
    from_state: CodeRequestState,
    to_state: CodeRequestState,
    override: bool,
) -> None:
    """Property test: transition() succeeds iff is_legal_transition() is True."""
    req = _sample_request(from_state)
    expected_legal = is_legal_transition(from_state, to_state, is_operator_override=override)

    if expected_legal:
        updated = transition(
            req,
            to_state,
            actor="operator-1",
            reason="testing transition",
            is_operator_override=override,
            now="2026-09-25T13:00:00Z",
        )
        assert updated.state == to_state
        assert updated.updated_at == "2026-09-25T13:00:00Z"
        assert len(updated.audit_trail) == len(req.audit_trail) + 1
        event = updated.audit_trail[-1]
        assert event.actor == "operator-1"
        assert event.from_state == from_state
        assert event.to_state == to_state
        assert event.reason == "testing transition"
        assert event.override == override
        assert event.timestamp == "2026-09-25T13:00:00Z"
    else:
        with pytest.raises(InvalidTransitionError):
            transition(
                req,
                to_state,
                actor="operator-1",
                reason="testing illegal transition",
                is_operator_override=override,
            )


def test_standard_happy_path() -> None:
    """End-to-end standard progression from draft to done."""
    req = _sample_request(CodeRequestState.DRAFT)

    req = transition(req, CodeRequestState.TRIAGE, actor="user", reason="Submit for triage")
    assert req.state == CodeRequestState.TRIAGE

    req = transition(req, CodeRequestState.PLANNING, actor="triage-bot", reason="Fast-tracked to planning")
    assert req.state == CodeRequestState.PLANNING

    req = transition(req, CodeRequestState.PLANNED, actor="planner", reason="Plan accepted")
    assert req.state == CodeRequestState.PLANNED

    req = transition(req, CodeRequestState.EXECUTING, actor="runner", reason="Job started")
    assert req.state == CodeRequestState.EXECUTING

    req = transition(req, CodeRequestState.DONE, actor="runner", reason="PR merged")
    assert req.state == CodeRequestState.DONE
    assert len(req.audit_trail) == 5


def test_board_review_paths() -> None:
    """Board review transitions: acceptance, deferral, decline."""
    # Accepted path
    req1 = _sample_request(CodeRequestState.TRIAGE)
    req1 = transition(req1, CodeRequestState.BOARD_REVIEW, actor="triage-bot", reason="Needs architecture review")
    assert req1.state == CodeRequestState.BOARD_REVIEW
    req1 = transition(req1, CodeRequestState.PLANNING, actor="board", reason="board:accepted")
    assert req1.state == CodeRequestState.PLANNING

    # Deferred path
    req2 = _sample_request(CodeRequestState.BOARD_REVIEW)
    req2 = transition(req2, CodeRequestState.DEFERRED, actor="board", reason="Waiting on dependency")
    assert req2.state == CodeRequestState.DEFERRED

    # Declined path
    req3 = _sample_request(CodeRequestState.BOARD_REVIEW)
    req3 = transition(req3, CodeRequestState.DECLINED, actor="board", reason="Out of charter scope")
    assert req3.state == CodeRequestState.DECLINED


def test_execution_failure() -> None:
    """Executing can fail."""
    req = _sample_request(CodeRequestState.EXECUTING)
    req = transition(req, CodeRequestState.FAILED, actor="runner", reason="Build timed out")
    assert req.state == CodeRequestState.FAILED


def test_operator_override_cancellation() -> None:
    """Operator override allows cancelling from any state."""
    for st_val in CodeRequestState:
        if st_val == CodeRequestState.CANCELLED:
            continue
        req = _sample_request(st_val)
        updated = transition(
            req,
            CodeRequestState.CANCELLED,
            actor="admin",
            reason="Operator cancelled",
            is_operator_override=True,
        )
        assert updated.state == CodeRequestState.CANCELLED


def test_operator_override_fast_track() -> None:
    """Operator override allows pre-planning states to jump directly to planning or board_review."""
    for pre in (CodeRequestState.DRAFT, CodeRequestState.TRIAGE, CodeRequestState.BOARD_REVIEW):
        req = _sample_request(pre)
        if pre != CodeRequestState.PLANNING:
            up_plan = transition(
                req,
                CodeRequestState.PLANNING,
                actor="admin",
                reason="Operator override to planning",
                is_operator_override=True,
            )
            assert up_plan.state == CodeRequestState.PLANNING

        if pre != CodeRequestState.BOARD_REVIEW:
            up_board = transition(
                req,
                CodeRequestState.BOARD_REVIEW,
                actor="admin",
                reason="Operator override to board",
                is_operator_override=True,
            )
            assert up_board.state == CodeRequestState.BOARD_REVIEW


def test_invalid_transitions_raise() -> None:
    """Cannot jump directly from draft to executing or done, even with override."""
    req = _sample_request(CodeRequestState.DRAFT)
    with pytest.raises(InvalidTransitionError):
        transition(req, CodeRequestState.EXECUTING, actor="admin", reason="Illegal jump", is_operator_override=True)
    with pytest.raises(InvalidTransitionError):
        transition(req, CodeRequestState.DONE, actor="admin", reason="Illegal jump", is_operator_override=True)

    # Unknown state string
    with pytest.raises(InvalidTransitionError, match="Unknown target state"):
        transition(req, "non_existent_state", actor="admin", reason="bad")
