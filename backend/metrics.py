"""System metrics endpoints for the runner dashboard.

Extracted from server.py as part of issue #159 god-module-refactor-2026q2.
"""

from __future__ import annotations

import datetime as _dt_mod

import psutil  # noqa: F401
from fastapi import APIRouter, Request

router = APIRouter(tags=["metrics"])
UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime


@router.get("/api/system")
async def get_system_metrics():
    """Real-time system resource metrics."""
    # Lazy import to avoid circular dependency
    from routers.system import (  # noqa: PLC0415
        _boot_time,
        _get_runner_capacity_snapshot,
        _host_memory_gb,
        _runner_limit,
    )
    from system_utils import get_system_metrics_snapshot  # noqa: PLC0415

    return await get_system_metrics_snapshot(
        runner_limit=_runner_limit(),
        boot_time=_boot_time,
        host_memory_gb=_host_memory_gb,
        get_runner_capacity_snapshot=_get_runner_capacity_snapshot,
    )


@router.get("/api/fleet/status")
async def get_fleet_status(request: Request):
    """Get full system metrics state for all machines in the fleet network."""
    # Lazy import to avoid circular dependency with server.py
    from server import (  # noqa: PLC0415
        FLEET_NODES,
        HOSTNAME,
        _classify_node_offline,
        _resource_offline_reason,
        _should_proxy_fleet_to_hub,
        proxy_to_hub,
    )

    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    responses = {}
    responses[HOSTNAME] = await get_system_metrics()
    responses[HOSTNAME]["_role"] = "hub"

    async def fetch_node(name, url):
        import httpx  # noqa: PLC0415

        try:
            async with httpx.AsyncClient() as client:
                target = f"{url}/api/system"
                resp = await client.get(target, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    data["_role"] = "node"
                    resource_reason = _resource_offline_reason(data)
                    if resource_reason:
                        data.update(resource_reason)
                    return name, data
                reason = _classify_node_offline(status_code=resp.status_code)
                return name, {
                    "status": "offline",
                    "error": reason["offline_detail"],
                    **reason,
                }
        except Exception as e:  # noqa: BLE001
            reason = _classify_node_offline(e)
            return name, {
                "status": "offline",
                "error": reason["offline_detail"],
                **reason,
            }

    if FLEET_NODES:
        import asyncio  # noqa: PLC0415

        results = await asyncio.gather(*[fetch_node(n, u) for n, u in FLEET_NODES.items()])
        for name, data in results:
            responses[name] = data

    return responses
