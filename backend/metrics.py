"""System metrics endpoints for the runner dashboard.

Extracted from server.py as part of issue #159 god-module-refactor-2026q2.
"""

from __future__ import annotations

import datetime as _dt_mod
from pathlib import Path

from fastapi import APIRouter, Request

router = APIRouter(tags=["metrics"])
UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime


def _resolve_windows_host_disk_path(configured_path: str | Path | None = None) -> Path | None:
    """Return the mounted Windows path that backs this runner pool's WSL VHDX.

    Operators can set RUNNER_WINDOWS_HOST_PATH for non-C: WSL installs such as
    the ControlTower-SSD runner pool on F:. The old hard-coded /mnt/c fallback remains for
    existing deployments, but it is no longer treated as universally correct.
    """
    candidates: list[Path] = []
    if configured_path:
        configured = Path(configured_path)
        candidates.append(configured)

    candidates.append(Path("/mnt/c"))

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


@router.get("/api/system")
async def get_system_metrics():
    """Real-time system resource metrics."""
    from routers.system import get_system_metrics as _canonical_system_metrics  # noqa: PLC0415

    return await _canonical_system_metrics()


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
