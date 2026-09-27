"""Integration tests for Code Request executor REST routes (CR-5, #1287; WP-2.2, #1606).

The pipeline is seeded from a filed plan (``initialize`` takes no children) and persisted
through a ``PipelineStore`` in ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from code_requests.executor_store import PipelineStore
from code_requests.model import CodeRequest, CodeRequestState, Requester, RequesterKind
from code_requests.plan import PlanDraft
from code_requests.plan_store import PlanSessionStore
from code_requests.planner import FilingProgress, PlanningSession, PlanningStatus
from code_requests.store import CodeRequestStore
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from routers import code_requests_executor as executor_router_module
from server import app

CR_ID = "cr-test-exec-1"
INIT = f"/api/code-requests/{CR_ID}/executor/initialize"


def _plan_child(key: str, deps: list[str] | None = None, tier: str = "tier:ollama") -> dict[str, Any]:
    return {
        "key": key,
        "title": f"Step {key}",
        "objective": f"Deliver {key}.",
        "execution_instructions": [f"Edit {key}"],
        "file_scope": {"allowed": [f"backend/{key}.py"]},
        "acceptance_criteria": [f"Criteria {key}"],
        "validation_commands": [{"command": "pytest", "expect": "pass"}],
        "dependencies": deps or [],
        "out_of_scope": ["UI"],
        "tier": tier,
        "complexity": "routine",
        "task_class": "feature",
        "next_steps": ["Start"],
    }


def _filed_plan(*children: dict[str, Any]) -> PlanningSession:
    draft = PlanDraft.model_validate(
        {"epic": {"title": "Epic", "summary": "s", "acceptance_criteria": ["done"]}, "children": list(children)}
    )
    numbers = {c["key"]: 50 + i for i, c in enumerate(children)}
    return PlanningSession(
        request_id=CR_ID,
        status=PlanningStatus.FILED,
        draft=draft,
        filing=FilingProgress(epic_number=49, children=numbers),
    )


def _make_code_request(req_id: str = CR_ID) -> CodeRequest:
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


@pytest.fixture
def sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PlanSessionStore:
    store = PlanSessionStore(tmp_path / "plans.json")
    monkeypatch.setattr(executor_router_module, "_plan_sessions", lambda: store)
    monkeypatch.setattr(executor_router_module, "_pipeline_store", lambda: PipelineStore(tmp_path / "pipelines.json"))
    return store


@pytest.fixture
def client(sessions: PlanSessionStore) -> TestClient:
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="test-operator",
        type="bot",
        name="Operator",
        roles=["operator", "admin"],
        scopes=["code-requests.manage", "code-requests.view", "operator"],
    )
    yield TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"}, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def mock_store(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    store = MagicMock(spec=CodeRequestStore)
    store.get = AsyncMock(return_value=_make_code_request())
    store.transition = AsyncMock()
    monkeypatch.setattr("routers.code_requests_executor._get_store", lambda: store)
    return store


class TestExecutorRoutes:
    def test_initialize_not_found(self, client: TestClient, mock_store: MagicMock):
        mock_store.get = AsyncMock(return_value=None)
        res = client.post("/api/code-requests/nonexistent/executor/initialize", json={})
        assert res.status_code == 404

    def test_initialize_cycle_validation_error(
        self, client: TestClient, mock_store: MagicMock, sessions: PlanSessionStore
    ):
        sessions.save(_filed_plan(_plan_child("step1", ["step2"]), _plan_child("step2", ["step1"])))
        res = client.post(INIT, json={})
        assert res.status_code == 422
        assert "dependency_cycle" in res.json()["detail"]
        mock_store.transition.assert_not_awaited()

    def test_initialize_and_rollup(self, client: TestClient, mock_store: MagicMock, sessions: PlanSessionStore):
        sessions.save(_filed_plan(_plan_child("step1"), _plan_child("step2", ["step1"])))
        init_res = client.post(INIT, json={})
        assert init_res.status_code == 200
        data = init_res.json()
        assert data["status"] == "initialized"
        assert len(data["rollup"]["waves"]) == 2
        assert data["rollup"]["children"]["step1"]["acceptance_criteria"] == ["Criteria step1"]
        assert data["rollup"]["children"]["step2"]["acceptance_criteria"] == ["Criteria step2"]

        rollup_res = client.get(f"/api/code-requests/{CR_ID}/executor/rollup")
        assert rollup_res.status_code == 200
        rollup = rollup_res.json()
        assert rollup["code_request_id"] == CR_ID
        assert set(rollup["children"]) == {"step1", "step2"}
        assert rollup["children"]["step2"]["issue_number"] == 51

    def test_dispatch_ready_children(
        self,
        client: TestClient,
        mock_store: MagicMock,
        sessions: PlanSessionStore,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # Plan children carry filed issue numbers, so dispatch runs the claim check and posts a lease.
        monkeypatch.setattr("code_requests.executor_stage.can_dispatch_child", lambda *a, **k: (True, "free"))
        monkeypatch.setattr("code_requests.executor_stage.post_child_lease", lambda *a, **k: (True, "lease-1"))
        sessions.save(_filed_plan(_plan_child("step1")))
        assert client.post(INIT, json={}).status_code == 200
        dispatch_res = client.post(f"/api/code-requests/{CR_ID}/executor/dispatch", json={})
        assert dispatch_res.status_code == 200
        assert "step1" in dispatch_res.json()["dispatched"]
        snap = PipelineStore(Path(executor_router_module._pipeline_store().path)).get(CR_ID)
        assert snap is not None and snap.children["step1"].lease_receipt == "lease-1"

    def test_dispatch_before_initialize_is_404(self, client: TestClient, mock_store: MagicMock):
        res = client.post(f"/api/code-requests/{CR_ID}/executor/dispatch", json={})
        assert res.status_code == 404

    def test_report_child_lifecycle(self, client: TestClient, mock_store: MagicMock, sessions: PlanSessionStore):
        sessions.save(_filed_plan(_plan_child("step1")))
        assert client.post(INIT, json={}).status_code == 200
        client.post(f"/api/code-requests/{CR_ID}/executor/dispatch", json={})
        report = f"/api/code-requests/{CR_ID}/executor/report-child"

        pr_res = client.post(report, json={"key": "step1", "event": "pr_opened", "pr_number": 201})
        assert pr_res.status_code == 200
        assert pr_res.json()["child"]["state"] == "pr_open"
        assert pr_res.json()["child"]["acceptance_criteria"] == ["Criteria step1"]

        ci_res = client.post(report, json={"key": "step1", "event": "ci_status", "ci_status": "running"})
        assert ci_res.status_code == 200
        assert ci_res.json()["child"]["state"] == "ci"

        merged_res = client.post(report, json={"key": "step1", "event": "merged", "cost": 0.05})
        assert merged_res.status_code == 200
        assert merged_res.json()["child"]["state"] == "merged"
        assert merged_res.json()["rollup_state"] == "done"
