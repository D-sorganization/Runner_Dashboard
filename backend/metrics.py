"""System metrics endpoints for the runner dashboard.

Extracted from server.py as part of issue #159 god-module-refactor-2026q2.
"""

from __future__ import annotations

from pathlib import Path

import psutil  # noqa: F401
from fastapi import APIRouter

router = APIRouter(tags=["metrics"])


def _resolve_windows_host_disk_path(
    configured_path: str | Path | None = None,
) -> Path | None:
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


# NOTE (issue #940): the GET /api/system and GET /api/fleet/status routes that
# previously lived here were duplicate registrations. Because this router is
# included before routers.system and routers.fleet in server.py, FastAPI's
# first-match-wins routing served these copies and shadowed the maintained
# implementations — silently killing the FleetEventPoller wiring that feeds
# /api/events (issue #863) and re-registering /api/system over routers.system.
# The canonical implementations now live in:
#   - GET /api/system        -> backend/routers/system.py
#   - GET /api/fleet/status  -> backend/routers/fleet.py (records fleet events)
# Do not re-add them here. A route-uniqueness invariant test enforces this.


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

                runner_backing_drive: str = storage.get("runner_backing_drive") or storage.get("host_drive", "")
                from host_volume import probe_host_volume  # noqa: PLC0415

                host_volume = probe_host_volume(drive=runner_backing_drive) if runner_backing_drive else None

                reports.append(
                    {
                        "pool_name": pool_name,
                        "parent_machine": pool.get("parent_machine", machine.get("name", "")),
                        "storage_tier": storage_tier,
                        "backing_disk": backing_disk,
                        "runner_backing_drive": runner_backing_drive,
                        "host_volume": host_volume,
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
