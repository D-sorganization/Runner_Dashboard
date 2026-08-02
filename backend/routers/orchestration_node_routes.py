"""Fleet node, hardware, and system proxy routes extracted from routers/orchestration.py."""

from __future__ import annotations

import datetime as _dt_mod
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import proxy_utils
from dashboard_config import FLEET_NODES, HOSTNAME
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import require_fleet_peer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard.orchestration")
router = APIRouter(tags=["orchestration"])


@dataclass(frozen=True, slots=True)
class OrchestrationNodeDeps:
    get_fleet_nodes_impl: Callable[[], Awaitable[dict[str, Any]]]
    get_system_metrics_snapshot: Callable[[], Awaitable[dict[str, Any]]]


def set_dependencies(
    app: FastAPI,
    get_fleet_nodes_impl: Callable,
    get_system_metrics_snapshot: Callable,
) -> None:
    """Wire server.py helpers into this route module through FastAPI app state."""
    app.state.orchestration_node_deps = OrchestrationNodeDeps(
        get_fleet_nodes_impl=get_fleet_nodes_impl,
        get_system_metrics_snapshot=get_system_metrics_snapshot,
    )


def orchestration_node_deps(request: Request) -> OrchestrationNodeDeps:
    deps = getattr(request.app.state, "orchestration_node_deps", None)
    if not isinstance(deps, OrchestrationNodeDeps):
        raise RuntimeError("orchestration node router dependencies are not configured")
    return deps


@router.get("/api/fleet/nodes", dependencies=[Depends(require_fleet_peer)])
async def get_fleet_nodes(
    request: Request,
    deps: OrchestrationNodeDeps = Depends(orchestration_node_deps),  # noqa: B008
) -> dict:
    """Aggregate system metrics + health from all fleet nodes."""
    if proxy_utils.should_proxy_fleet_to_hub(request):
        return await proxy_utils.proxy_to_hub(request)
    return await deps.get_fleet_nodes_impl()


@router.get("/api/fleet/hardware", dependencies=[Depends(require_fleet_peer)])
async def get_fleet_hardware(
    request: Request,
    deps: OrchestrationNodeDeps = Depends(orchestration_node_deps),  # noqa: B008
) -> dict:
    """Return centralized fleet hardware specs for workload placement."""
    if proxy_utils.should_proxy_fleet_to_hub(request):
        return await proxy_utils.proxy_to_hub(request)
    fleet = await deps.get_fleet_nodes_impl()
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
async def proxy_node_system(
    node_name: str,
    deps: OrchestrationNodeDeps = Depends(orchestration_node_deps),  # noqa: B008
) -> dict:
    """Proxy /api/system from a named fleet node (for detailed drill-down)."""
    if node_name in (HOSTNAME, "local"):
        return await deps.get_system_metrics_snapshot()
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
