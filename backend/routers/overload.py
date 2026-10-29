"""GET /api/v1/fleet/overload — per-repo swarm overload detection (#3293 slice 1).

Read-only. See ``coordination/overload.py`` for the data contract.
"""

# ruff: noqa: B008
from __future__ import annotations

from typing import Any

from cache_utils import cache_get, cache_set
from coordination.overload import DEFAULT_OPEN_COUNT_THRESHOLD, build_overload_snapshot
from fastapi import APIRouter, Depends, Query
from identity import require_fleet_peer
from security import validate_owner_repo_format

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet-overload"])

_CACHE_TTL_S = 20.0


def _cache_key(repo: str, open_count_threshold: int) -> str:
    return f"fleet:overload:v1:{repo}:{open_count_threshold}"


@router.get("/overload")
async def get_fleet_overload(
    repo: str = Query(..., max_length=200),
    open_count_threshold: int = Query(default=DEFAULT_OPEN_COUNT_THRESHOLD, ge=1, le=10_000),
    _peer: str = Depends(require_fleet_peer),
) -> Any:
    repo = validate_owner_repo_format(repo)

    key = _cache_key(repo, open_count_threshold)
    snapshot = cache_get(key, _CACHE_TTL_S)
    if snapshot is None:
        snapshot = await build_overload_snapshot(repo, open_count_threshold=open_count_threshold)
        cache_set(key, snapshot)
    return snapshot
