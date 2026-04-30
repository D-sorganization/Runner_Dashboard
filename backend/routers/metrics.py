"""System metrics collection and aggregation routes.

Handles:
  - /api/fleet/hardware — fleet hardware specs and workload placement
  - /api/fleet/nodes/{node_name}/system — system metrics (proxied or local)

Note: Helper functions for metrics collection are defined in server.py to avoid
circular imports and to support multiple route handlers across the application.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from dashboard_config import HOSTNAME
from fastapi import APIRouter, HTTPException, Request
from system_utils import get_system_metrics_snapshot
from utils.utilities import _should_proxy_fleet_to_hub, proxy_to_hub

UTC = timezone.utc  # noqa: UP017
log = logging.getLogger("dashboard.metrics")

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/fleet/hardware")
async def get_fleet_hardware(request: Request) -> dict:
    """Return centralized fleet hardware specs for workload placement."""
    # Import here to avoid circular dependency
    from server import _get_fleet_nodes_impl

    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    fleet = await _get_fleet_nodes_impl()
    machines = []
    for node in fleet.get("nodes", []):
        registry = node.get("registry") or {}
        specs = node.get("hardware_specs") or node.get("system", {}).get("hardware_specs", {})
        capacity = node.get("workload_capacity") or node.get("system", {}).get("workload_capacity", {})
        machines.append(
            {
                "name": node.get("name"),
                "display_name": registry.get("display_name") or node.get("name"),
                "online": bool(node.get("online")),
                "dashboard_reachable": bool(node.get("dashboard_reachable")),
                "role": registry.get("role") or node.get("role"),
                "runner_labels": registry.get("runner_labels", []),
                "hardware_specs": specs,
                "workload_capacity": capacity,
                "offline_reason": node.get("offline_reason"),
            }
        )
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "machines": machines,
        "count": len(machines),
        "online_count": sum(1 for machine in machines if machine["online"]),
        "registry": fleet.get("registry", {}),
    }


@router.get("/fleet/nodes/{node_name}/system")
async def proxy_node_system(node_name: str) -> dict:
    """Proxy /api/system from a named fleet node (for detailed drill-down)."""
    from dashboard_config import FLEET_NODES

    if node_name in (HOSTNAME, "local"):
        return await get_system_metrics_snapshot()
    url = FLEET_NODES.get(node_name)
    if not url:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_name}")

    import httpx

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{url}/api/system")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Node returned error")
        return resp.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"{node_name} timed out") from exc
    except httpx.RequestError as exc:
        log.warning("Node %s unreachable: %s", node_name, exc)
        raise HTTPException(status_code=502, detail=f"{node_name} unreachable") from exc
