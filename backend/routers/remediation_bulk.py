"""Bulk PR and Issue agent dispatch routes (SC-G5-4 / #1500)."""

from __future__ import annotations

import os
from pathlib import Path

import agent_dispatch_router
import quota_enforcement
from dashboard_config import ORG
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from identity import Principal, require_scope
from system_utils import run_cmd

from .remediation import _normalize_repository_input

router = APIRouter(tags=["remediation"])

REPO_ROOT = Path(os.environ.get("RUNNER_DASHBOARD_REPO_ROOT", Path(__file__).parents[2]))


@router.post("/api/prs/dispatch", response_model=None)
async def api_dispatch_to_prs(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("github.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch agents to one or more pull requests."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")
    try:
        req = agent_dispatch_router.PRDispatchRequest(**body)
        req.principal = principal.id
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Wave 3: Quota and Fair Sharing
    allowed, reason = quota_enforcement.quota_enforcement.check_dispatch_quota(principal, estimated_cost=0.10)
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Quota exceeded: {reason}")

    result = await agent_dispatch_router.dispatch_to_prs(
        req,
        run_cmd_fn=run_cmd,
        org=ORG,
        repo_root=REPO_ROOT,
        normalize_repository_fn=_normalize_repository_input,
    )
    if isinstance(result, dict) and "error" in result:
        status_code = int(result.get("status_code", 400))
        if status_code == 429:
            retry_after = int(result.get("retry_after", 60))
            return JSONResponse(  # type: ignore[return-value]
                status_code=429,
                content={"detail": result["error"], "retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        raise HTTPException(status_code=status_code, detail=result["error"])
    if isinstance(result, agent_dispatch_router.BulkDispatchResponse):
        return result.model_dump()
    return dict(result)


@router.post("/api/issues/dispatch", response_model=None)
async def api_dispatch_to_issues(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("github.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch agents to one or more issues."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")
    try:
        req = agent_dispatch_router.IssueDispatchRequest(**body)
        req.principal = principal.id
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Wave 3: Quota and Fair Sharing
    allowed, reason = quota_enforcement.quota_enforcement.check_dispatch_quota(principal, estimated_cost=0.10)
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Quota exceeded: {reason}")

    result = await agent_dispatch_router.dispatch_to_issues(
        req,
        run_cmd_fn=run_cmd,
        org=ORG,
        repo_root=REPO_ROOT,
        normalize_repository_fn=_normalize_repository_input,
    )
    if isinstance(result, dict) and "error" in result:
        status_code = int(result.get("status_code", 400))
        if status_code == 429:
            retry_after = int(result.get("retry_after", 60))
            return JSONResponse(  # type: ignore[return-value]
                status_code=429,
                content={"detail": result["error"], "retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        raise HTTPException(status_code=status_code, detail=result["error"])
    if isinstance(result, agent_dispatch_router.BulkDispatchResponse):
        return result.model_dump()
    return dict(result)
