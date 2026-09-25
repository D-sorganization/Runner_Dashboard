"""Tests for Code Requests Board routing gate endpoints (CR-6, issue #1286)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from code_requests.model import (
    BoardRoute,
    CodeRequest,
    CodeRequestState,
    Requester,
    RequesterKind,
)
from code_requests.store import CodeRequestStore
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from proposals.models import ProposalDetail, ProposalItem
from server import app


def _make_code_request(
    req_id: str = "cr-100",
    state: CodeRequestState = CodeRequestState.TRIAGE,
    title: str = "Test feature",
    prompt: str = "Do something",
    board_route: BoardRoute = BoardRoute.AUTO,
    board_proposal: str | None = None,
) -> CodeRequest:
    return CodeRequest(
        id=req_id,
        issue_number=100,
        title=title,
        repository="Runner_Dashboard",
        prompt=prompt,
        state=state,
        board_route=board_route,
        board_proposal=board_proposal,
        requester=Requester(id="local", kind=RequesterKind.AGENT),
        created_at="2026-09-25T12:00:00Z",
        updated_at="2026-09-25T12:00:00Z",
    )


@pytest.fixture
def client_admin() -> TestClient:
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="test-admin", type="bot", name="Admin", roles=["admin"]
    )
    yield TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"}, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def client_non_operator() -> TestClient:
    # Principal with code-requests.manage but NOT operator role
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="test-manager", type="bot", name="Manager", scopes=["code-requests.manage"]
    )
    yield TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"}, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def mock_store(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    store = MagicMock(spec=CodeRequestStore)
    monkeypatch.setattr("routers.code_requests_board._get_store", lambda: store)
    return store


class TestEvaluateBoardRoute:
    def test_evaluate_not_found(self, client_admin: TestClient, mock_store: MagicMock) -> None:
        mock_store.get = AsyncMock(return_value=None)
        resp = client_admin.post(
            "/api/code-requests/cr-missing/evaluate-board",
            json={"criteria": {}},
        )
        assert resp.status_code == 404

    def test_evaluate_routine_request(self, client_admin: TestClient, mock_store: MagicMock) -> None:
        req = _make_code_request(title="Routine bugfix", prompt="Fix simple typo")
        mock_store.get = AsyncMock(return_value=req)

        resp = client_admin.post(
            "/api/code-requests/cr-100/evaluate-board",
            json={"criteria": {"new_surface": False}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"]["routes_to_board"] is False

    def test_evaluate_triggered_request(self, client_admin: TestClient, mock_store: MagicMock) -> None:
        req = _make_code_request(title="Add new UI tab", prompt="Create new tab")
        mock_store.get = AsyncMock(return_value=req)

        resp = client_admin.post(
            "/api/code-requests/cr-100/evaluate-board",
            json={"criteria": {"new_surface": True}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"]["routes_to_board"] is True
        assert "new_surface" in data["decision"]["criteria_matched"]


class TestRouteToBoardEndpoint:
    def test_route_to_board_not_found(self, client_admin: TestClient, mock_store: MagicMock) -> None:
        mock_store.get = AsyncMock(return_value=None)
        resp = client_admin.post(
            "/api/code-requests/cr-missing/route-to-board",
            json={"criteria": {}},
        )
        assert resp.status_code == 404

    def test_route_routine_bypasses_board_to_planning(self, client_admin: TestClient, mock_store: MagicMock) -> None:
        req = _make_code_request(title="Routine fix", prompt="Fix typo")
        mock_store.get = AsyncMock(return_value=req)
        planning_req = req.model_copy(update={"state": CodeRequestState.PLANNING})
        mock_store.transition = AsyncMock(return_value=planning_req)

        resp = client_admin.post(
            "/api/code-requests/cr-100/route-to-board",
            json={"criteria": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["routed"] is False
        assert data["decision"]["routes_to_board"] is False
        assert data["request"]["state"] == "planning"
        mock_store.transition.assert_called_once()

    def test_route_to_board_creates_proposal(self, client_admin: TestClient, mock_store: MagicMock) -> None:
        req = _make_code_request(title="New DB Schema", prompt="Migrate database schema")
        mock_store.get = AsyncMock(return_value=req)
        mock_store.save = AsyncMock(side_effect=lambda r: r)

        mock_proposal = ProposalItem(
            number=777,
            title=req.title,
            target_repos=["Runner_Dashboard"],
            problem=req.prompt,
            evidence="Evidence",
            options_considered="Options",
            lean="Lean",
            estimated_cost="Medium",
            urgency="Routine",
            source="local",
            code_request_url="https://github.com/D-sorganization/Runner_Dashboard/issues/100",
            state="open",
            decision=None,
            decision_labels=[],
            html_url="https://github.com/D-sorganization/Repository_Management/issues/777",
            created_at="2026-09-25T12:00:00Z",
            updated_at="2026-09-25T12:00:00Z",
        )

        with patch("proposals.service.create_proposal", AsyncMock(return_value=mock_proposal)):
            resp = client_admin.post(
                "/api/code-requests/cr-100/route-to-board",
                json={"criteria": {"cross_repo_contract": True}},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["routed"] is True
            assert data["decision"]["routes_to_board"] is True
            assert data["request"]["state"] == "board_review"
            assert data["request"]["board_proposal"] == "777"

    def test_operator_override_requires_operator_role(
        self, client_non_operator: TestClient, mock_store: MagicMock
    ) -> None:
        req = _make_code_request()
        mock_store.get = AsyncMock(return_value=req)

        resp = client_non_operator.post(
            "/api/code-requests/cr-100/route-to-board",
            json={
                "board_route": "force_board",
                "is_operator_override": True,
                "override_reason": "Operator tested override",
            },
        )
        assert resp.status_code == 403

    def test_operator_override_requires_reason(self, client_admin: TestClient, mock_store: MagicMock) -> None:
        req = _make_code_request()
        mock_store.get = AsyncMock(return_value=req)

        resp = client_admin.post(
            "/api/code-requests/cr-100/route-to-board",
            json={
                "board_route": "force_board",
                "is_operator_override": True,
                "override_reason": "",
            },
        )
        assert resp.status_code == 422


class TestSyncBoardDecisionEndpoint:
    def test_sync_no_proposal(self, client_admin: TestClient, mock_store: MagicMock) -> None:
        req = _make_code_request(board_proposal=None)
        mock_store.get = AsyncMock(return_value=req)

        resp = client_admin.post("/api/code-requests/cr-100/sync-board-decision")
        assert resp.status_code == 200
        assert resp.json()["synced"] is False

    def test_sync_accepted_decision(self, client_admin: TestClient, mock_store: MagicMock) -> None:
        req = _make_code_request(
            state=CodeRequestState.BOARD_REVIEW,
            board_proposal="777",
        )
        mock_store.get = AsyncMock(return_value=req)
        mock_store.save = AsyncMock(side_effect=lambda r: r)

        detail = ProposalDetail(
            number=777,
            title="New DB Schema",
            target_repos=["Runner_Dashboard"],
            problem="Problem",
            evidence="Evidence",
            options_considered="Options",
            lean="Lean",
            estimated_cost="Medium",
            urgency="Routine",
            source="local",
            code_request_url="url",
            state="decided",
            decision="accepted",
            decision_labels=["board:accepted"],
            html_url="url",
            created_at="time",
            updated_at="time",
            comments=[],
        )

        with patch("proposals.service.get_proposal", AsyncMock(return_value=detail)):
            resp = client_admin.post("/api/code-requests/cr-100/sync-board-decision")
            assert resp.status_code == 200
            data = resp.json()
            assert data["synced"] is True
            assert data["request"]["state"] == "planning"


class TestEscalationDeadlineEndpoint:
    def test_escalation_endpoint(self, client_admin: TestClient, mock_store: MagicMock) -> None:
        req = _make_code_request(state=CodeRequestState.BOARD_REVIEW, board_proposal="777")
        mock_store.get = AsyncMock(return_value=req)

        resp = client_admin.get("/api/code-requests/cr-100/board-escalation?meetings_elapsed=2&max_meetings=2")
        assert resp.status_code == 200
        assert resp.json()["escalation_needed"] is True
