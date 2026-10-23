"""Authenticated, non-federating staff run routes for peer-to-peer lookup."""

# ruff: noqa: B008

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from identity import Principal, require_scope
from routers.staff import MAX_LIMIT, cancel_run, get_run, stream_run
from staff.models import StaffCancelResponse, StaffRunDetailResponse

router = APIRouter(prefix="/api/staff", tags=["staff"])


def _local_miss(run_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "local_run_not_found", "run_id": run_id},
    )


@router.get(
    "/local-runs/{run_id}",
    response_model=StaffRunDetailResponse,
    response_model_exclude_none=True,
    include_in_schema=False,
)
async def get_local_run(
    run_id: str,
    events: int = Query(default=200, ge=0, le=MAX_LIMIT),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Return this node's run detail without any peer lookup.

    The distinct path is intentional: older nodes treat ``local=1`` as an
    ordinary query parameter and may recursively search their peers.
    """
    try:
        return await get_run(run_id=run_id, events=events, local=True, _peer=_peer)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise _local_miss(run_id) from exc
        raise


@router.get("/local-runs/{run_id}/stream", include_in_schema=False)
async def stream_local_run(
    run_id: str,
    after: int = Query(default=0, ge=0),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> StreamingResponse:
    """Stream only a run stored on this node; never federate the lookup."""
    try:
        return await stream_run(run_id=run_id, after=after, local=True, _peer=_peer)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise _local_miss(run_id) from exc
        raise


@router.post(
    "/local-runs/{run_id}/cancel",
    response_model=StaffCancelResponse,
    response_model_exclude_none=True,
    include_in_schema=False,
)
async def cancel_local_run(
    run_id: str,
    caller: Principal = Depends(require_scope("staff.cancel")),
) -> dict[str, Any]:
    """Cancel only a run stored on this node; never federate the lookup."""
    try:
        return await cancel_run(run_id=run_id, local=True, caller=caller)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise _local_miss(run_id) from exc
        raise
