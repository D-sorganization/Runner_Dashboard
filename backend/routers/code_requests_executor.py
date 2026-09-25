"""REST endpoints for Code Request executor stage (CR-5, #1287).

Endpoints:
- POST /api/code-requests/{id}/executor/initialize: Initialize executor with planned children
- POST /api/code-requests/{id}/executor/dispatch: Dispatch ready children in the current wave
- POST /api/code-requests/{id}/executor/report-child: Report child progress, PR, CI or failure
- GET /api/code-requests/{id}/executor/rollup: Get status rollup and per-child states and costs
"""

from __future__ import annotations

import logging
from typing import Any

from code_requests.executor_models import (
    ChildIssuePayload,
    ExecutionConfig,
    ExecutorRollup,
)
from code_requests.executor_stage import ExecutorPipeline
from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, require_scope
from pydantic import BaseModel, Field
from routers.code_requests import _get_store

log = logging.getLogger("dashboard.code_requests.executor_router")

router = APIRouter(tags=["code-requests-executor"])

_ACTIVE_PIPELINES: dict[str, ExecutorPipeline] = {}


def _get_pipeline(cr_id: str) -> ExecutorPipeline:
    pipeline = _ACTIVE_PIPELINES.get(cr_id)
    if pipeline is None:
        pipeline = ExecutorPipeline(code_request_id=cr_id)
        _ACTIVE_PIPELINES[cr_id] = pipeline
    return pipeline


class InitializeExecutorPayload(BaseModel):
    children: list[ChildIssuePayload] = Field(min_length=1)
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
    """Initialize the executor pipeline with a list of planned child issues."""
    store = _get_store()
    request = await store.get(id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Code Request {id!r} not found")

    pipeline = ExecutorPipeline(
        code_request_id=id,
        config=payload.config,
        session_id=principal.id or "executor-api",
    )
    try:
        pipeline.initialize(payload.children, request=request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _ACTIVE_PIPELINES[id] = pipeline
    rollup = pipeline.get_rollup()
    return {"status": "initialized", "rollup": rollup.model_dump()}


@router.post("/api/code-requests/{id}/executor/dispatch")
async def dispatch_executor(
    id: str,
    payload: DispatchChildrenPayload,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict[str, Any]:
    """Dispatch ready children in the current wave."""
    store = _get_store()
    request = await store.get(id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Code Request {id!r} not found")

    pipeline = _get_pipeline(id)
    target_keys = payload.keys or [c.key for c in pipeline.get_ready_children()]
    dispatched: list[str] = []
    skipped: dict[str, str] = {}

    for k in target_keys:
        ok, reason = pipeline.dispatch_child(k, request=request)
        if ok:
            dispatched.append(k)
        else:
            skipped[k] = reason

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
    store = _get_store()
    request = await store.get(id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Code Request {id!r} not found")

    pipeline = _get_pipeline(id)
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

    rollup = pipeline.get_rollup()
    return {"status": "reported", "child": pipeline.children[payload.key].model_dump(), "rollup_state": rollup.state}


@router.get("/api/code-requests/{id}/executor/rollup")
async def get_executor_rollup(
    id: str,
    *,
    principal: Principal = Depends(require_scope("code-requests.view")),  # noqa: B008
) -> ExecutorRollup:
    """Get the current aggregate status rollup for a Code Request plan."""
    store = _get_store()
    request = await store.get(id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Code Request {id!r} not found")

    pipeline = _get_pipeline(id)
    return pipeline.get_rollup()
