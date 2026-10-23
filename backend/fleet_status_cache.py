"""Stale-while-revalidate cache for ``GET /api/fleet/status`` (#2110)."""

from __future__ import annotations

import copy
import os
from collections.abc import Awaitable, Callable
from typing import Any

from cache_utils import cache_get, cache_get_swr
from dashboard_config import HttpTimeout

FLEET_STATUS_FRESH_TTL_S = float(os.environ.get("RUNNER_DASHBOARD_FLEET_STATUS_FRESH_TTL_S", "10"))
FLEET_STATUS_STALE_TTL_S = float(os.environ.get("RUNNER_DASHBOARD_FLEET_STATUS_STALE_TTL_S", "120"))
FLEET_STATUS_WAIT_TIMEOUT_S = float(
    os.environ.get("RUNNER_DASHBOARD_FLEET_STATUS_WAIT_S", str(HttpTimeout.FLEET_NODE_PROBE_READ_S + 1.0))
)

FLEET_STATUS_CACHE_PREFIX = "fleet:status:v1"


def fleet_status_cache_key(*, exclude_pools: bool) -> str:
    return f"{FLEET_STATUS_CACHE_PREFIX}:exclude_pools={int(exclude_pools)}"


async def serve_fleet_status(
    cache_key: str,
    collect: Callable[[], Awaitable[dict[str, Any]]],
    *,
    wait_timeout: float | None = None,
) -> dict[str, Any]:
    """Return fleet status fresh, stale-while-revalidate, or a bounded cold wait."""
    wait = FLEET_STATUS_WAIT_TIMEOUT_S if wait_timeout is None else wait_timeout
    fresh = cache_get(cache_key, FLEET_STATUS_FRESH_TTL_S)
    if fresh is not None:
        return fresh

    stale_before = cache_get(cache_key, FLEET_STATUS_STALE_TTL_S)

    def on_timeout() -> dict[str, Any]:
        stale = cache_get(cache_key, FLEET_STATUS_STALE_TTL_S) or stale_before
        if stale is not None:
            return {**copy.deepcopy(stale), "_stale": True}
        return {"_stale": True, "_warming": True}

    result = await cache_get_swr(
        cache_key,
        collect,
        fresh_ttl=FLEET_STATUS_FRESH_TTL_S,
        stale_ttl=FLEET_STATUS_STALE_TTL_S,
        wait_timeout=wait,
        on_timeout=on_timeout,
    )

    if stale_before is not None and cache_get(cache_key, FLEET_STATUS_FRESH_TTL_S) is None:
        if result.get("_warming"):
            return result
        return {**result, "_stale": True}
    return result
