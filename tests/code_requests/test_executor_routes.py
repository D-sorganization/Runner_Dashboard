"""Integration tests for Code Request executor REST routes (CR-5, #1287)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from code_requests.model import CodeRequest, CodeRequestState, Requester, RequesterKind
from code_requests.store import CodeRequestStore
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from routers import code_requests_executor as executor_router_module
from server import app


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="test-operator",
        type="bot",
        name="Operator",
        roles=["operator", "admin"],
        scopes=["code-requests.manage", "code-requests.view", "operator"],
    )
    yield TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"}, raise_server_exceptions=False)
    app.dependency_overrides.clear()
    executor_router_module._ACTIVE_PIPELINES.clear()


@pytest.fixture
def mock_store(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    store = MagicMock(spec=CodeRequestStore)
    monkeypatch.setattr("routers.code_requests_executor._get_store", lambda: store)
    return store


def _make_code_request(req_id: str = "cr-test-exec-1") -> CodeRequest:
    return CodeRequest(
        id=req_id,
        repository="Runner_Dashboard",
        title="Test Code Request",
        issue_number=100,
        state=CodeRequestState.PLANNED,
        requester=Requester(id="tester", kind=RequesterKind.HUMAN),
        created_at="2026-09-25T12:00:00Z",
        updated_at="2026-09-25T12:00:00Z",
    )


class TestExecutorRoutes:
    def test_initialize_not_found(self, client: TestClient, mock_store: MagicMock):
        mock_store.get = AsyncMock(return_value=None)
        res = client.post(
            "/api/code-requests/nonexistent/executor/initialize",
            json={"children": [{"key": "task1", "title": "Task 1", "repository": "repo"}]},
        )
        assert res.status_code == 404

    def test_initialize_cycle_validation_error(self, client: TestClient, mock_store: MagicMock):
        mock_store.get = AsyncMock(return_value=_make_code_request())
        res = client.post(
            "/api/code-requests/cr-test-exec-1/executor/initialize",
            json={
                "children": [
                    {"key": "A", "title": "A", "repository": "repo", "dependencies": ["B"]},
                    {"key": "B", "title": "B", "repository": "repo", "dependencies": ["A"]},
                ]
            },
        )
        assert res.status_code == 422
        assert "dependency_cycle" in res.json()["detail"]

    def test_initialize_and_rollup(self, client: TestClient, mock_store: MagicMock):
        mock_store.get = AsyncMock(return_value=_make_code_request())
        init_res = client.post(
            "/api/code-requests/cr-test-exec-1/executor/initialize",
            json={
                "children": [
                    {"key": "step1", "title": "Step 1", "repository": "Runner_Dashboard"},
                    {"key": "step2", "title": "Step 2", "repository": "Runner_Dashboard", "dependencies": ["step1"]},
                ]
            },
        )
        assert init_res.status_code == 200
        data = init_res.json()
        assert data["status"] == "initialized"
        assert len(data["rollup"]["waves"]) == 2

        # Get rollup
        rollup_res = client.get("/api/code-requests/cr-test-exec-1/executor/rollup")
        assert rollup_res.status_code == 200
        rollup = rollup_res.json()
        assert rollup["code_request_id"] == "cr-test-exec-1"
        assert "step1" in rollup["children"]
        assert "step2" in rollup["children"]

    def test_dispatch_ready_children(self, client: TestClient, mock_store: MagicMock):
        mock_store.get = AsyncMock(return_value=_make_code_request())
        client.post(
            "/api/code-requests/cr-test-exec-1/executor/initialize",
            json={
                "children": [
                    {"key": "step1", "title": "Step 1", "repository": "Runner_Dashboard", "tier": "ollama"},
                ]
            },
        )
        dispatch_res = client.post(
            "/api/code-requests/cr-test-exec-1/executor/dispatch",
            json={},
        )
        assert dispatch_res.status_code == 200
        disp = dispatch_res.json()
        assert "step1" in disp["dispatched"]

    def test_report_child_lifecycle(self, client: TestClient, mock_store: MagicMock):
        mock_store.get = AsyncMock(return_value=_make_code_request())
        client.post(
            "/api/code-requests/cr-test-exec-1/executor/initialize",
            json={
                "children": [
                    {"key": "step1", "title": "Step 1", "repository": "Runner_Dashboard", "issue_number": 50},
                ]
            },
        )
        client.post("/api/code-requests/cr-test-exec-1/executor/dispatch", json={})

        # Report PR opened
        pr_res = client.post(
            "/api/code-requests/cr-test-exec-1/executor/report-child",
            json={"key": "step1", "event": "pr_opened", "pr_number": 201},
        )
        assert pr_res.status_code == 200
        assert pr_res.json()["child"]["state"] == "pr_open"

        # Report CI running
        ci_res = client.post(
            "/api/code-requests/cr-test-exec-1/executor/report-child",
            json={"key": "step1", "event": "ci_status", "ci_status": "running"},
        )
        assert ci_res.status_code == 200
        assert ci_res.json()["child"]["state"] == "ci"

        # Report merged
        merged_res = client.post(
            "/api/code-requests/cr-test-exec-1/executor/report-child",
            json={"key": "step1", "event": "merged", "cost": 0.05},
        )
        assert merged_res.status_code == 200
        assert merged_res.json()["child"]["state"] == "merged"
        assert merged_res.json()["rollup_state"] == "done"
