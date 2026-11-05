"""Flow metrics read API (#3818, PD-4)."""

from __future__ import annotations

from typing import Any

from coordination.models import REPO_PATTERN
from fastapi import APIRouter, Depends, Query
from flow_metrics import collect_flow_metrics
from identity import require_fleet_peer
from routers.repo_query import bare_repo_query as _repo

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet-flow-metrics"])


@router.get("/flow-metrics")
async def read_flow_metrics(
    repo: str | None = Query(default=None, pattern=REPO_PATTERN),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    """Per-repo flow metrics: open PR age, merge cycle time, weekly issue burn-down."""
    return await collect_flow_metrics(repo=_repo(repo))
