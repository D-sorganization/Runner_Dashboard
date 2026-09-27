"""WP-2.2 (#1606): the executor is built from the filed plan, drives planned -> executing, and persists.

- Children come from the plan session (``draft`` plus ``filing.children``), never the caller.
- ``initialize`` needs a ``planned`` Code Request with a filed plan, and moves it to ``executing``.
- Pipeline state survives a restart (a fresh store instance reads what the last one saved).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from code_requests.executor_models import ChildExecutionState, ExecutorTier  # noqa: E402
from code_requests.executor_plan import PlanNotFiledError, children_from_plan  # noqa: E402
from code_requests.executor_stage import ExecutorPipeline  # noqa: E402
from code_requests.executor_store import PipelineStore  # noqa: E402
from code_requests.model import CodeRequest, CodeRequestState, Requester, RequesterKind  # noqa: E402
from code_requests.plan import PlanDraft  # noqa: E402
from code_requests.plan_store import PlanSessionStore  # noqa: E402
from code_requests.planner import FilingProgress, PlanningSession, PlanningStatus  # noqa: E402
from code_requests.store import CodeRequestStore  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from identity import Principal, require_principal  # noqa: E402
from routers import code_requests_executor as executor_router  # noqa: E402
from server import app  # noqa: E402

CR_ID = "cr-plan-1"


def _child(key: str, deps: list[str] | None = None, tier: str = "tier:cli") -> dict[str, Any]:
    return {
        "key": key,
        "title": f"Implement {key}",
        "objective": f"Deliver {key}.",
        "execution_instructions": [f"Edit backend/{key}.py"],
        "file_scope": {"allowed": [f"backend/{key}.py", f"tests/test_{key}.py"], "forbidden": ["frontend/"]},
        "acceptance_criteria": [f"{key} behaves as specified", f"{key} is tested"],
        "validation_commands": [{"command": f"pytest tests/test_{key}.py", "expect": "all tests pass"}],
        "dependencies": deps or [],
        "out_of_scope": ["UI changes"],
        "tier": tier,
        "complexity": "routine",
        "task_class": "feature",
        "next_steps": [f"Write the failing test for {key}"],
    }


def _session(*, filed: bool = True, numbers: dict[str, int] | None = None) -> PlanningSession:
    draft = PlanDraft.model_validate(
        {
            "epic": {"title": "Epic", "summary": "Two slices.", "acceptance_criteria": ["All merged"]},
            "children": [_child("a", tier="tier:strong"), _child("b", ["a", "#77"])],
        }
    )
    return PlanningSession(
        request_id=CR_ID,
        status=PlanningStatus.FILED if filed else PlanningStatus.DRAFT,
        draft=draft,
        filing=FilingProgress(epic_number=500, children=numbers if numbers is not None else {"a": 501, "b": 502}),
    )


def _request(state: CodeRequestState = CodeRequestState.PLANNED) -> CodeRequest:
    return CodeRequest(
        id=CR_ID,
        repository="Runner_Dashboard",
        title="Widget pipeline",
        issue_number=100,
        state=state,
        requester=Requester(id="tester", kind=RequesterKind.HUMAN),
        created_at="2026-09-27T12:00:00Z",
        updated_at="2026-09-27T12:00:00Z",
    )


# ── children come from the filed plan ────────────────────────────────────────
@pytest.mark.unit
def test_children_are_built_from_the_filed_plan() -> None:
    children = children_from_plan(_session(), repository="Runner_Dashboard")
    assert [c.key for c in children] == ["a", "b"]
    a, b = children
    assert (a.issue_number, b.issue_number) == (501, 502)
    assert (a.tier, b.tier) == (ExecutorTier.STRONG, ExecutorTier.CLI)
    assert b.dependencies == ["a", "#77"]
    assert a.acceptance_criteria == ["a behaves as specified", "a is tested"]
    assert a.file_scope == ["backend/a.py", "tests/test_a.py"]
    assert a.repository == "Runner_Dashboard"


@pytest.mark.unit
def test_an_unfiled_plan_or_an_unfiled_child_is_refused() -> None:
    with pytest.raises(PlanNotFiledError, match="not filed"):
        children_from_plan(_session(filed=False), repository="Runner_Dashboard")
    with pytest.raises(PlanNotFiledError, match="'b'"):
        children_from_plan(_session(numbers={"a": 501}), repository="Runner_Dashboard")
    with pytest.raises(PlanNotFiledError, match="no plan"):
        children_from_plan(None, repository="Runner_Dashboard")


# ── pipeline state persists ─────────────────────────────────────────────────
@pytest.mark.unit
def test_pipeline_survives_a_restart(tmp_path: Path) -> None:
    pipeline = ExecutorPipeline(code_request_id=CR_ID)
    pipeline.initialize(children_from_plan(_session(), repository="Runner_Dashboard"))
    pipeline.children["a"].state = ChildExecutionState.MERGED
    pipeline.paused_branches.add("b")
    PipelineStore(tmp_path / "pipelines.json").save(pipeline.snapshot())

    snap = PipelineStore(tmp_path / "pipelines.json").get(CR_ID)
    assert snap is not None
    restored = ExecutorPipeline.from_snapshot(snap)
    assert restored.get_rollup() == pipeline.get_rollup()
    assert restored.children["a"].state == ChildExecutionState.MERGED
    assert restored.paused_branches == {"b"}
    assert PipelineStore(tmp_path / "pipelines.json").get("other") is None


# ── the initialize route ────────────────────────────────────────────────────
@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    requests = MagicMock(spec=CodeRequestStore)
    requests.get = AsyncMock(return_value=_request())
    requests.transition = AsyncMock(return_value=_request(CodeRequestState.EXECUTING))
    sessions = PlanSessionStore(tmp_path / "plans.json")
    sessions.save(_session())
    pipelines_path = tmp_path / "pipelines.json"
    monkeypatch.setattr(executor_router, "_get_store", lambda: requests)
    monkeypatch.setattr(executor_router, "_plan_sessions", lambda: sessions)
    monkeypatch.setattr(executor_router, "_pipeline_store", lambda: PipelineStore(pipelines_path))
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="op", type="human", name="Op", roles=["operator"], scopes=["code-requests.manage", "code-requests.view"]
    )
    yield {"requests": requests, "sessions": sessions, "pipelines": pipelines_path}
    app.dependency_overrides.clear()


def _client() -> TestClient:
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"}, raise_server_exceptions=False)


@pytest.mark.unit
def test_initialize_builds_from_the_plan_and_moves_to_executing(env: dict[str, Any]) -> None:
    res = _client().post(f"/api/code-requests/{CR_ID}/executor/initialize", json={})
    assert res.status_code == 200, res.text
    assert set(res.json()["rollup"]["children"]) == {"a", "b"}
    env["requests"].transition.assert_awaited_once()
    args, kwargs = env["requests"].transition.await_args
    assert args[:2] == (CR_ID, CodeRequestState.EXECUTING)
    assert "#500" in kwargs["reason"]
    assert PipelineStore(env["pipelines"]).get(CR_ID) is not None


@pytest.mark.unit
def test_initialize_refuses_caller_supplied_children(env: dict[str, Any]) -> None:
    res = _client().post(
        f"/api/code-requests/{CR_ID}/executor/initialize",
        json={"children": [{"key": "x", "title": "x", "repository": "r", "acceptance_criteria": ["c"]}]},
    )
    assert res.status_code == 422
    env["requests"].transition.assert_not_awaited()


@pytest.mark.unit
def test_initialize_needs_a_planned_request(env: dict[str, Any]) -> None:
    env["requests"].get = AsyncMock(return_value=_request(CodeRequestState.TRIAGE))
    res = _client().post(f"/api/code-requests/{CR_ID}/executor/initialize", json={})
    assert res.status_code == 409 and "planned" in res.text
    env["requests"].transition.assert_not_awaited()


@pytest.mark.unit
def test_initialize_needs_a_filed_plan(env: dict[str, Any]) -> None:
    env["sessions"].save(_session(filed=False))
    res = _client().post(f"/api/code-requests/{CR_ID}/executor/initialize", json={})
    assert res.status_code == 409 and "not filed" in res.text
    env["requests"].transition.assert_not_awaited()


@pytest.mark.unit
def test_routes_read_the_persisted_pipeline(env: dict[str, Any]) -> None:
    client = _client()
    assert client.get(f"/api/code-requests/{CR_ID}/executor/rollup").status_code == 404
    assert client.post(f"/api/code-requests/{CR_ID}/executor/initialize", json={}).status_code == 200
    report = client.post(
        f"/api/code-requests/{CR_ID}/executor/report-child",
        json={"key": "a", "event": "failed", "reason": "flaky"},
    )
    assert report.status_code == 200, report.text
    # A new store instance stands in for a restart: the report above was persisted.
    snap = PipelineStore(env["pipelines"]).get(CR_ID)
    assert snap is not None and snap.children["a"].attempts == 1
    rollup = client.get(f"/api/code-requests/{CR_ID}/executor/rollup")
    assert rollup.status_code == 200 and rollup.json()["children"]["a"]["attempts"] == 1
