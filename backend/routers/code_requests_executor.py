"""REST endpoints for Code Request executor stage (CR-5, #1287; WP-2.2, #1606).

Endpoints:
- POST /api/code-requests/{id}/executor/initialize: Build the executor from the filed plan and start executing
- POST /api/code-requests/{id}/executor/dispatch: Dispatch ready children in the current wave
- POST /api/code-requests/{id}/executor/report-child: Report child progress, PR, CI or failure
- GET /api/code-requests/{id}/executor/rollup: Get status rollup and per-child states and costs

Pipelines persist in ``code_request_pipelines.json`` next to the Code Request store, so a
restart resumes where execution stopped.
"""

from __future__ import annotations

import logging
from typing import Any

from code_requests.executor_models import ExecutionConfig, ExecutorRollup
from code_requests.executor_plan import PlanNotFiledError, children_from_plan
from code_requests.executor_stage import ExecutorPipeline
from code_requests.executor_store import PipelineStore
from code_requests.model import CodeRequest, CodeRequestState
from code_requests.plan_store import PlanSessionStore
from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, require_scope
from pydantic import BaseModel, ConfigDict, Field
from routers import code_request_plans
from routers.code_requests import _active_storage_path, _get_store

log = logging.getLogger("dashboard.code_requests.executor_router")

router = APIRouter(tags=["code-requests-executor"])


def _plan_sessions() -> PlanSessionStore:
    return code_request_plans._sessions


def _pipeline_store() -> PipelineStore:
    return PipelineStore(_active_storage_path().with_name("code_request_pipelines.json"))


async def _get_request(cr_id: str) -> CodeRequest:
    request = await _get_store().get(cr_id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Code Request {cr_id!r} not found")
    return request


def _load_pipeline(cr_id: str) -> ExecutorPipeline:
    snap = _pipeline_store().get(cr_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Code Request {cr_id!r} has no executor pipeline")
    return ExecutorPipeline.from_snapshot(snap)


def _save_pipeline(pipeline: ExecutorPipeline) -> None:
    _pipeline_store().save(pipeline.snapshot())


class InitializeExecutorPayload(BaseModel):
    """Children always come from the filed plan; the caller may only tune execution."""

    model_config = ConfigDict(extra="forbid")

    config: ExecutionConfig | None = None


class DispatchChildrenPayload(BaseModel):
    keys: list[str] | None = None


class ReportChildPayload(BaseModel):
    key: str
    event: str  # "pr_opened", "ci_status", "merged", "failed"
    pr_number: int | None = None
    pr_body: str = ""
    pr_labels: list[str] = Field(default_factory=list)
    ci_status: str = ""
    reason: str = ""
    cost: float = 0.0
    updated_handoff: str = ""


@router.post("/api/code-requests/{id}/executor/initialize")
async def initialize_executor(
    id: str,
    payload: InitializeExecutorPayload,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict[str, Any]:
    """Build the executor from the filed plan and move the Code Request ``planned -> executing``.

    Pre: the request is ``planned`` and its plan is filed (409 otherwise).
    Post: the pipeline is persisted and the request is ``executing``.
    """
    request = await _get_request(id)
    if request.state is not CodeRequestState.PLANNED:
        raise HTTPException(status_code=409, detail=f"Code Request must be planned (state {request.state.value!r})")
    session = _plan_sessions().get(id)
    try:
        children = children_from_plan(session, repository=request.repository)
    except PlanNotFiledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    pipeline = ExecutorPipeline(code_request_id=id, config=payload.config, session_id=principal.id or "executor-api")
    try:
        pipeline.initialize(list(children), request=request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    assert session is not None  # children_from_plan refused a missing session
    await _get_store().transition(
        id,
        CodeRequestState.EXECUTING,
        actor=principal.id,
        reason=f"executor initialized from plan #{session.filing.epic_number}",
    )
    _save_pipeline(pipeline)
    return {"status": "initialized", "rollup": pipeline.get_rollup().model_dump()}


@router.post("/api/code-requests/{id}/executor/dispatch")
async def dispatch_executor(
    id: str,
    payload: DispatchChildrenPayload,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict[str, Any]:
    """Dispatch ready children in the current wave."""
    request = await _get_request(id)
    pipeline = _load_pipeline(id)
    target_keys = payload.keys or [c.key for c in pipeline.get_ready_children()]
    dispatched: list[str] = []
    skipped: dict[str, str] = {}

    for k in target_keys:
        ok, reason = pipeline.dispatch_child(k, request=request)
        if ok:
            dispatched.append(k)
        else:
            skipped[k] = reason

    _save_pipeline(pipeline)
    return {
        "dispatched": dispatched,
        "skipped": skipped,
        "current_wave": pipeline.current_wave_idx,
    }


@router.post("/api/code-requests/{id}/executor/report-child")
async def report_child_status(
    id: str,
    payload: ReportChildPayload,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict[str, Any]:
    """Report progress, PR, CI or failure event on a child issue."""
    await _get_request(id)
    pipeline = _load_pipeline(id)
    if payload.key not in pipeline.children:
        raise HTTPException(status_code=404, detail=f"Child issue {payload.key!r} not in pipeline")

    ev = payload.event.lower()
    if ev == "pr_opened":
        if payload.pr_number is None:
            raise HTTPException(status_code=422, detail="pr_number required for pr_opened event")
        pipeline.report_pr_opened(payload.key, payload.pr_number, pr_body=payload.pr_body, pr_labels=payload.pr_labels)
    elif ev == "ci_status":
        pipeline.report_ci_status(payload.key, payload.ci_status)
    elif ev == "merged":
        pipeline.report_merged(payload.key, cost=payload.cost)
    elif ev == "failed":
        pipeline.report_failed(
            payload.key,
            reason=payload.reason or "unspecified_failure",
            cost=payload.cost,
            updated_handoff=payload.updated_handoff,
        )
    else:
        raise HTTPException(status_code=422, detail=f"Unknown event type: {payload.event!r}")

    _save_pipeline(pipeline)
    rollup = pipeline.get_rollup()
    return {"status": "reported", "child": pipeline.children[payload.key].model_dump(), "rollup_state": rollup.state}


@router.get("/api/code-requests/{id}/executor/rollup")
async def get_executor_rollup(
    id: str,
    *,
    principal: Principal = Depends(require_scope("code-requests.view")),  # noqa: B008
) -> ExecutorRollup:
    """Get the current aggregate status rollup for a Code Request plan."""
    await _get_request(id)
    pipeline = _load_pipeline(id)
    return pipeline.get_rollup()
