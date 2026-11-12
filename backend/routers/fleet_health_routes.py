"""Fleet host health gauge routes (#4310)."""

from __future__ import annotations

from typing import Any

import httpx
from dashboard_config import FLEET_NODES, HOSTNAME
from fastapi import APIRouter, Depends, HTTPException
from host_health import build_host_health_gauge
from identity import require_fleet_peer
from routers.orchestration_node_routes import OrchestrationNodeDeps, orchestration_node_deps

router = APIRouter(tags=["fleet"])


@router.get("/api/fleet/hosts/{host}/health", dependencies=[Depends(require_fleet_peer)])
async def get_host_health(
    host: str,
    deps: OrchestrationNodeDeps = Depends(orchestration_node_deps),  # noqa: B008
) -> dict[str, Any]:
    """Return green/amber/red health gauge and capacity context for one host."""
    if host in (HOSTNAME, "local"):
        system = await deps.get_system_metrics_snapshot()
        return build_host_health_gauge(HOSTNAME or host, system)
    url = FLEET_NODES.get(host)
    if not url:
        raise HTTPException(status_code=404, detail=f"Node not found: {host}")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{url}/api/system")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Node returned error")
        system = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"{host} unreachable") from exc
    gauge = build_host_health_gauge(host, system if isinstance(system, dict) else {})
    gauge["proxied"] = True
    return gauge
