"""System metrics endpoints for the runner dashboard.

Extracted from server.py as part of issue #159 god-module-refactor-2026q2.
"""

from __future__ import annotations

import datetime as _dt_mod
from pathlib import Path

import psutil  # noqa: F401
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
async def get_fleet_status(request: Request, exclude_pools: bool = False):
    """Get full system metrics state for all machines in the fleet network."""
    # Lazy import to avoid circular dependency with server.py
    from dashboard_config import PORT  # noqa: PLC0415
    from server import (  # noqa: PLC0415
        FLEET_NODES,
        _classify_node_offline,
        _resource_offline_reason,
        _should_proxy_fleet_to_hub,
        proxy_to_hub,
    )

    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    responses = {}
    local_metrics = await get_system_metrics()
    local_metrics["_role"] = "hub"

    if PORT == 8322:
        local_pool_name = "ControlTower-HDD"
        peer_pool_name = "ControlTower-NVMe"
        peer_port = 8321
    else:
        local_pool_name = "ControlTower-NVMe"
        peer_pool_name = "ControlTower-HDD"
        peer_port = 8322

    responses[local_pool_name] = local_metrics

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

    if not exclude_pools:
        import logging  # noqa: PLC0415

        import httpx  # noqa: PLC0415

        logger = logging.getLogger("dashboard.metrics")
        peer_url = f"http://localhost:{peer_port}/api/fleet/status?exclude_pools=true"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(peer_url, timeout=5)
                if resp.status_code == 200:
                    peer_data = resp.json()
                    for k, v in peer_data.items():
                        responses[k] = v
                else:
                    reason = _classify_node_offline(status_code=resp.status_code)
                    responses[peer_pool_name] = {
                        "status": "offline",
                        "error": reason["offline_detail"],
                        **reason,
                    }
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to query peer pool on port %d: %s", peer_port, e)
            reason = _classify_node_offline(e)
            responses[peer_pool_name] = {
                "status": "offline",
                "error": reason["offline_detail"],
                **reason,
            }

    return responses


@router.get("/api/disk/pool-pressure")
async def get_pool_disk_pressure():
    """Storage-tier aware disk pressure report for all registered runner pools.

    Returns per-pool pressure classification using tier-specific thresholds:
    - NVMe pools: IO saturation is the primary constraint
    - HDD/SSD pools: capacity is the primary constraint

    Each pool entry includes:
      - ``pool_name``       — registry name
      - ``storage_tier``    — nvme / hdd / ssd / unknown
      - ``backing_disk``    — WSL mount path of the backing Windows drive
      - ``vhdx_path``       — VHDX path if known
      - ``disk_bus``        — bus type from registry (NVMe, SATA, etc.)
      - ``disk_media_type`` — media type from registry (SSD, HDD, etc.)
      - ``free_gb``, ``used_gb``, ``total_gb``, ``percent`` — live capacity
      - ``io_pressure_full_avg10`` — Linux PSI IO stall (if available)
      - ``pressure``        — tier-aware classification (low/medium/high/critical)

    DbC: this endpoint is read-only; it never mutates state.
    """
    import asyncio  # noqa: PLC0415
    import shutil  # noqa: PLC0415

    from machine_registry import load_machine_registry  # noqa: PLC0415
    from system_utils import (  # noqa: PLC0415
        classify_disk_pressure_by_tier,
        get_host_disk_for_pool,
        get_io_pressure_snapshot,
    )

    def _build_pool_report() -> list[dict]:
        registry = load_machine_registry()
        io_snapshot = get_io_pressure_snapshot()
        io_full_avg10: float = 0.0
        if io_snapshot and isinstance(io_snapshot.get("full"), dict):
            io_full_avg10 = float(io_snapshot["full"].get("avg10", 0.0))

        reports: list[dict] = []
        for machine in registry.get("machines", []):
            for pool in machine.get("runner_pools", []):
                pool_name: str = pool.get("name", "")
                storage_tier: str = pool.get("storage_tier", "")
                storage: dict = pool.get("storage") or {}
                vhdx_path: str | None = storage.get("vhdx_path")
                disk_bus: str = storage.get("disk_bus", "")
                disk_media_type: str = storage.get("disk_media_type", "")

                backing_disk = get_host_disk_for_pool(pool)
                runner_base_dir: str = pool.get("runner_base_dir", "")

                # Prefer runner_base_dir for capacity (it's inside the VHDX);
                # fall back to the host disk mount for free-space.
                measure_path = runner_base_dir if runner_base_dir else backing_disk
                import os  # noqa: PLC0415

                if not os.path.exists(measure_path):
                    measure_path = backing_disk

                try:
                    du = shutil.disk_usage(measure_path)
                    total_gb = round(du.total / (1024**3), 1)
                    used_gb = round(du.used / (1024**3), 1)
                    free_gb = round(du.free / (1024**3), 1)
                    percent = round(du.used / du.total * 100, 1) if du.total else 0.0
                except OSError:
                    total_gb = used_gb = free_gb = percent = 0.0

                tier_pressure = classify_disk_pressure_by_tier(
                    storage_tier=storage_tier,
                    percent=percent,
                    free_gb=free_gb,
                    io_pressure_full_avg10=io_full_avg10,
                )

                reports.append(
                    {
                        "pool_name": pool_name,
                        "parent_machine": pool.get("parent_machine", machine.get("name", "")),
                        "storage_tier": storage_tier,
                        "backing_disk": backing_disk,
                        "vhdx_path": vhdx_path,
                        "disk_bus": disk_bus,
                        "disk_media_type": disk_media_type,
                        "runner_base_dir": runner_base_dir,
                        "measure_path": measure_path,
                        "total_gb": total_gb,
                        "used_gb": used_gb,
                        "free_gb": free_gb,
                        "percent": percent,
                        "io_pressure_full_avg10": io_full_avg10,
                        "pressure": tier_pressure,
                    }
                )
        return reports

    reports = await asyncio.to_thread(_build_pool_report)
    return {"pools": reports, "pool_count": len(reports)}
