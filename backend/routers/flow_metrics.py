"""Flow metrics read API (#3818, PD-4)."""

from __future__ import annotations

from typing import Any

from coordination.models import REPO_PATTERN, bare_repo
from fastapi import APIRouter, Depends, HTTPException, Query
from flow_metrics import collect_flow_metrics
from identity import require_fleet_peer

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet-flow-metrics"])


def _repo(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return bare_repo(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/flow-metrics")
async def read_flow_metrics(
    repo: str | None = Query(default=None, pattern=REPO_PATTERN),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    """Per-repo flow metrics: open PR age, merge cycle time, weekly issue burn-down."""
    return await collect_flow_metrics(repo=_repo(repo))
