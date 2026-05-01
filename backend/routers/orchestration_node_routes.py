"""Fleet node, hardware, and system proxy routes extracted from routers/orchestration.py."""

from __future__ import annotations

import datetime as _dt_mod
import logging
from typing import TYPE_CHECKING

import httpx
from dashboard_config import FLEET_NODES, HOSTNAME
from fastapi import APIRouter, HTTPException, Request

if TYPE_CHECKING:
    from collections.abc import Callable

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard.orchestration")
router = APIRouter(tags=["orchestration"])

_get_fleet_nodes_impl: Callable | None = None
_proxy_to_hub: Callable | None = None
_should_proxy_fleet_to_hub: Callable | None = None
_get_system_metrics_snapshot: Callable | None = None


def set_dependencies(
    get_fleet_nodes_impl: Callable,
    proxy_to_hub: Callable,
    should_proxy_fleet_to_hub: Callable,
    get_system_metrics_snapshot: Callable,
) -> None:
    """Wire server.py helpers into this route module."""
    global _get_fleet_nodes_impl, _proxy_to_hub, _should_proxy_fleet_to_hub  # noqa: PLW0603
    global _get_system_metrics_snapshot  # noqa: PLW0603
    _get_fleet_nodes_impl = get_fleet_nodes_impl
    _proxy_to_hub = proxy_to_hub
    _should_proxy_fleet_to_hub = should_proxy_fleet_to_hub
    _get_system_metrics_snapshot = get_system_metrics_snapshot


@router.get("/api/fleet/nodes")
async def get_fleet_nodes(request: Request) -> dict:
    """Aggregate system metrics + health from all fleet nodes."""
    if _should_proxy_fleet_to_hub(request):  # type: ignore[misc]
        return await _proxy_to_hub(request)  # type: ignore[misc]
    return await _get_fleet_nodes_impl()  # type: ignore[misc]


@router.get("/api/fleet/hardware")
async def get_fleet_hardware(request: Request) -> dict:
    """Return centralized fleet hardware specs for workload placement."""
    if _should_proxy_fleet_to_hub(request):  # type: ignore[misc]
        return await _proxy_to_hub(request)  # type: ignore[misc]
    fleet = await _get_fleet_nodes_impl()  # type: ignore[misc]
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


@router.get("/api/fleet/nodes/{node_name}/system")
async def proxy_node_system(node_name: str) -> dict:
    """Proxy /api/system from a named fleet node (for detailed drill-down)."""
    if node_name in (HOSTNAME, "local"):
        return await _get_system_metrics_snapshot()  # type: ignore[misc]
    url = FLEET_NODES.get(node_name)
    if not url:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_name}")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{url}/api/system")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Node returned error")
        return resp.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"{node_name} timed out") from exc
    except httpx.RequestError as exc:
        log.warning("Node %s unreachable: %s", node_name, exc)
        raise HTTPException(status_code=502, detail=f"{node_name} unreachable") from exc
