"""Read-only review-flow metrics API for the hub review-pool decision gate (#4250)."""

from __future__ import annotations

from typing import Any

from coordination.models import REPO_PATTERN
from fastapi import APIRouter, Depends, Query
from identity import require_fleet_peer
from routers.repo_query import bare_repo_query as _repo
from staff.review_metrics import collect_review_metrics
from staff.runner import get_runner

router = APIRouter(prefix="/api/v1/staff", tags=["staff-review-metrics"])


@router.get("/review/metrics")
async def read_review_metrics(
    repo: str | None = Query(default=None, pattern=REPO_PATTERN),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    """Queue depth, verdict mix, provider availability and 429s for the review flow.

    Deferred by design: review latency p50/p90 and reviewer disagreement rate need
    the per-head-SHA verdict contract from #4249, which does not exist yet.
    """
    return await collect_review_metrics(repo=_repo(repo), database=get_runner().store.path)
