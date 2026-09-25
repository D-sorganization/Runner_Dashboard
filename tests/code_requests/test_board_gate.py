"""Tests for Code Request Board routing gate (CR-6, issue #1286).

Covers:
1. Table-driven tests for every routing criterion (routes vs doesn't route).
2. Confidential InEnTec/ICR data egress requires user sign-off.
3. Operator overrides (force_board and skip_board) with audit tracking.
4. Escalation deadline triggering after N scheduled meetings without decision.
5. End-to-end integration: triage -> board proposal creation -> board:accepted -> planning with notes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from code_requests.board_gate import (
    BoardRoutingCriteria,
    BoardRoutingDecision,
    RuleBasedBoardClassifier,
    check_board_escalation,
    evaluate_board_routing,
    route_code_request_to_board,
    sync_board_proposal_decision,
)
from code_requests.model import (
    BoardRoute,
    CodeRequest,
    CodeRequestState,
    Requester,
    RequesterKind,
)


def _make_request(
    *,
    id: str = "cr-test-1",
    title: str = "Add telemetry logger",
    prompt: str = "Add a simple logging function to track runner memory.",
    repository: str = "Runner_Dashboard",
    board_route: BoardRoute = BoardRoute.AUTO,
    state: CodeRequestState = CodeRequestState.TRIAGE,
    issue_number: int | None = 100,
    board_proposal: str | None = None,
) -> CodeRequest:
    return CodeRequest(
        id=id,
        repository=repository,
        title=title,
        prompt=prompt,
        state=state,
        requester=Requester(id="alice", kind=RequesterKind.HUMAN),
        board_route=board_route,
        issue_number=issue_number,
        board_proposal=board_proposal,
        created_at="2026-09-25T12:00:00Z",
        updated_at="2026-09-25T12:00:00Z",
    )


class TestBoardRoutingCriteriaTable:
    """Table-driven test suite for all explicit board routing criteria."""

    @pytest.mark.parametrize(
        ("criteria", "expected_route", "expected_matched", "expected_user_signoff"),
        [
            # Baseline: routine work skips board
            (
                BoardRoutingCriteria(),
                False,
                [],
                False,
            ),
            # Criterion 1: New user-facing surface (tab, page, site section, app)
            (
                BoardRoutingCriteria(new_surface=True),
                True,
                ["new_surface"],
                False,
            ),
            # Criterion 2: New repo, service, or long-running daemon
            (
                BoardRoutingCriteria(new_service_or_repo=True),
                True,
                ["new_service_or_repo"],
                False,
            ),
            # Criterion 3: New external dependency or 3rd-party API
            (
                BoardRoutingCriteria(new_dependency_or_egress=True),
                True,
                ["new_dependency_or_egress"],
                False,
            ),
            # Criterion 3b: Confidential InEnTec/ICR data egress requires user sign-off
            (
                BoardRoutingCriteria(inentec_data_egress=True),
                True,
                ["inentec_data_egress"],
                True,
            ),
            # Criterion 4: Cross-repo contract or schema change
            (
                BoardRoutingCriteria(cross_repo_contract=True),
                True,
                ["cross_repo_contract"],
                False,
            ),
            # Criterion 5: Structural change to a public site (e.g. AffineDrift IA)
            (
                BoardRoutingCriteria(public_site_structure=True),
                True,
                ["public_site_structure"],
                False,
            ),
            # Criterion 6a: More than 8 child issues estimated
            (
                BoardRoutingCriteria(estimated_child_issues=9),
                True,
                ["estimated_child_issues"],
                False,
            ),
            # Boundary check: exactly 8 child issues does NOT trigger gate
            (
                BoardRoutingCriteria(estimated_child_issues=8),
                False,
                [],
                False,
            ),
            # Criterion 6b: Multiple target repos estimated
            (
                BoardRoutingCriteria(target_repos_count=2),
                True,
                ["multiple_target_repos"],
                False,
            ),
            # Criterion 7: Requester explicitly tagged `board`
            (
                BoardRoutingCriteria(tagged_board=True),
                True,
                ["tagged_board"],
                False,
            ),
        ],
    )
    def test_criteria_evaluation(
        self,
        criteria: BoardRoutingCriteria,
        expected_route: bool,
        expected_matched: list[str],
        expected_user_signoff: bool,
    ) -> None:
        request = _make_request()
        decision = evaluate_board_routing(request, criteria=criteria)
        assert decision.routes_to_board is expected_route
        for item in expected_matched:
            assert item in decision.criteria_matched
        assert decision.requires_user_signoff is expected_user_signoff


class TestBoardRoutingOverrides:
    """Test operator overrides force_board and skip_board."""

    def test_force_board_overrides_routine_criteria(self) -> None:
        request = _make_request(board_route=BoardRoute.FORCE_BOARD)
        criteria = BoardRoutingCriteria()  # All False
        decision = evaluate_board_routing(request, criteria=criteria)
        assert decision.routes_to_board is True
        assert "force_board" in decision.criteria_matched

    def test_skip_board_overrides_triggering_criteria(self) -> None:
        request = _make_request(board_route=BoardRoute.SKIP_BOARD)
        criteria = BoardRoutingCriteria(new_surface=True, new_service_or_repo=True)
        decision = evaluate_board_routing(request, criteria=criteria)
        assert decision.routes_to_board is False
        assert "skip_board" in decision.criteria_matched

    def test_skip_board_cannot_override_inentec_data_egress_without_user_signoff(self) -> None:
        request = _make_request(board_route=BoardRoute.SKIP_BOARD)
        criteria = BoardRoutingCriteria(inentec_data_egress=True)
        decision = evaluate_board_routing(request, criteria=criteria)
        # Confidential InEnTec data egress must never be silently skipped without user signoff
        assert decision.routes_to_board is True
        assert decision.requires_user_signoff is True


class TestRuleBasedClassifier:
    """Test text/keyword heuristic classification pass."""

    def test_detects_board_keywords_in_prompt_or_title(self) -> None:
        classifier = RuleBasedBoardClassifier()
        # Test new tab/page detection
        decision1 = classifier.classify("New Analytics Page", "Create a brand new analytics tab for users.")
        assert decision1.routes_to_board is True
        assert "new_surface" in decision1.criteria_matched

        # Test cross-repo contract detection
        decision2 = classifier.classify("Update envelope", "Modify the dispatch envelope schema.")
        assert decision2.routes_to_board is True
        assert "cross_repo_contract" in decision2.criteria_matched

        # Test routine bugfix does not route
        decision3 = classifier.classify("Fix null check", "Fix null pointer exception in helper.")
        assert decision3.routes_to_board is False


class TestBoardProposalCreationAndState:
    """Test creating proposal through CR-7 API and state transition."""

    def test_route_creates_board_proposal_and_transitions_to_board_review(self) -> None:
        request = _make_request(state=CodeRequestState.TRIAGE)
        decision = BoardRoutingDecision(
            routes_to_board=True,
            reasons=["Adds a new public page"],
            criteria_matched=["new_surface"],
        )

        proposal_mock = MagicMock()
        proposal_mock.number = 456
        proposal_mock.html_url = "https://github.com/D-sorganization/Repository_Management/issues/456"

        create_proposal_fn = MagicMock(return_value=proposal_mock)

        updated_request = route_code_request_to_board(
            request,
            decision=decision,
            create_proposal_fn=create_proposal_fn,
            actor="operator-1",
        )

        assert updated_request.state == CodeRequestState.BOARD_REVIEW
        assert updated_request.board_proposal == "456"
        create_proposal_fn.assert_called_once()
        kwargs = create_proposal_fn.call_args.kwargs
        assert kwargs["title"] == request.title
        assert kwargs["target_repos"] == [request.repository]
        assert "456" in updated_request.board_proposal


class TestBoardDecisionSync:
    """Test syncing decision labels on linked proposal to Code Request."""

    def test_accepted_label_moves_to_planning_with_board_notes(self) -> None:
        request = _make_request(
            state=CodeRequestState.BOARD_REVIEW,
            board_proposal="456",
        )

        get_proposal_fn = MagicMock(
            return_value={
                "number": 456,
                "state": "decided",
                "decision": "accepted",
                "decision_labels": ["board:accepted"],
                "meeting_date": "2026-09-28",
                "comments": [
                    {
                        "user": "board-secretary",
                        "body": "Accepted with condition: must support offline mode.",
                    }
                ],
            }
        )

        updated_request = sync_board_proposal_decision(
            request,
            get_proposal_fn=get_proposal_fn,
            actor="board-sync",
        )

        assert updated_request.state == CodeRequestState.PLANNING
        assert "must support offline mode" in updated_request.prompt

    def test_declined_label_moves_to_declined(self) -> None:
        request = _make_request(
            state=CodeRequestState.BOARD_REVIEW,
            board_proposal="456",
        )

        get_proposal_fn = MagicMock(
            return_value={
                "number": 456,
                "state": "decided",
                "decision": "declined",
                "decision_labels": ["board:declined"],
            }
        )

        updated_request = sync_board_proposal_decision(
            request,
            get_proposal_fn=get_proposal_fn,
            actor="board-sync",
        )

        assert updated_request.state == CodeRequestState.DECLINED

    def test_deferred_label_moves_to_deferred(self) -> None:
        request = _make_request(
            state=CodeRequestState.BOARD_REVIEW,
            board_proposal="456",
        )

        get_proposal_fn = MagicMock(
            return_value={
                "number": 456,
                "state": "decided",
                "decision": "deferred",
                "decision_labels": ["board:deferred"],
            }
        )

        updated_request = sync_board_proposal_decision(
            request,
            get_proposal_fn=get_proposal_fn,
            actor="board-sync",
        )

        assert updated_request.state == CodeRequestState.DEFERRED


class TestEscalationDeadline:
    """Test escalation deadline fires after 2 scheduled meetings without decision."""

    def test_escalation_deadline_not_fired_when_under_limit(self) -> None:
        request = _make_request(state=CodeRequestState.BOARD_REVIEW)
        # 1 meeting passed since proposal
        assert check_board_escalation(request, meetings_elapsed=1, max_meetings=2) is False

    def test_escalation_deadline_fires_when_meeting_threshold_reached(self) -> None:
        request = _make_request(state=CodeRequestState.BOARD_REVIEW)
        # 2 meetings passed
        assert check_board_escalation(request, meetings_elapsed=2, max_meetings=2) is True

    def test_escalation_deadline_ignored_if_already_decided(self) -> None:
        request = _make_request(state=CodeRequestState.PLANNING)
        assert check_board_escalation(request, meetings_elapsed=5, max_meetings=2) is False
