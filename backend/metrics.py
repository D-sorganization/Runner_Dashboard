"""System metrics endpoints for the runner dashboard.

Extracted from server.py as part of issue #159 god-module-refactor-2026q2.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["metrics"])


@router.get("/api/system")
async def get_system_metrics():
    """Real-time system resource metrics."""
    # Lazy import to avoid circular dependency with server.py
    from server import (  # noqa: PLC0415
        BOOT_TIME,
        HOST_MEMORY_GB,
        HOSTNAME,
        RUNNER_BASE_DIR,
        UTC,
        Path,
        _cpu_history,
        _disk_pressure_snapshot,
        _local_hardware_specs,
        _workload_capacity_from_specs,
        datetime,
        get_gpu_info,
        get_per_runner_resources,
        get_runner_capacity_snapshot,
        os,
        platform,
        psutil,
        shutil,
        time,
    )

    cpu_freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk_path = str(RUNNER_BASE_DIR) if RUNNER_BASE_DIR.exists() else "/"
    disk = shutil.disk_usage(disk_path)
    disk_total_gb = round(disk.total / (1024**3), 1)
    disk_used_gb = round(disk.used / (1024**3), 1)
    disk_free_gb = round(disk.free / (1024**3), 1)
    disk_percent = round(disk.used / disk.total * 100, 1)

    if os.name == "nt":
        net = psutil.net_io_counters()
        per_cpu = psutil.cpu_percent(interval=0, percpu=True)
        current_cpu = psutil.cpu_percent(interval=0)
        _cpu_history.append(current_cpu)
        cpu_avg_1m = round(sum(_cpu_history) / len(_cpu_history), 1) if _cpu_history else current_cpu
        uptime_seconds = time.time() - psutil.boot_time()
        dashboard_uptime = time.time() - BOOT_TIME
        disk_pressure = _disk_pressure_snapshot(
            path=disk_path,
            total_gb=disk_total_gb,
            used_gb=disk_used_gb,
            free_gb=disk_free_gb,
            percent=disk_percent,
        )
        hardware_specs = _local_hardware_specs(None)
        return {
            "hostname": HOSTNAME,
            "platform": platform.platform(),
            "timestamp": datetime.now(UTC).isoformat(),
            "uptime_seconds": int(uptime_seconds),
            "dashboard_uptime_seconds": int(dashboard_uptime),
            "cpu": {
                "cores_physical": psutil.cpu_count(logical=False),
                "cores_logical": psutil.cpu_count(logical=True),
                "percent": current_cpu,
                "percent_1m_avg": cpu_avg_1m,
                "per_cpu_percent": per_cpu,
                "freq_current_mhz": round(cpu_freq.current, 0) if cpu_freq else None,
                "freq_max_mhz": round(cpu_freq.max, 0) if cpu_freq else None,
                "load_avg_1m": 0,
                "load_avg_5m": 0,
                "load_avg_15m": 0,
            },
            "memory": {
                "host_total_gb": round(mem.total / (1024**3), 1),
                "wsl_total_gb": None,
                "total_gb": round(mem.total / (1024**3), 1),
                "used_gb": round(mem.used / (1024**3), 1),
                "available_gb": round(mem.available / (1024**3), 1),
                "percent": mem.percent,
                "swap_total_gb": round(swap.total / (1024**3), 1),
                "swap_used_gb": round(swap.used / (1024**3), 1),
                "swap_percent": swap.percent,
            },
            "disk": {
                "path": disk_path,
                "total_gb": disk_total_gb,
                "used_gb": disk_used_gb,
                "free_gb": disk_free_gb,
                "percent": disk_percent,
                "pressure": disk_pressure,
                "windows_host": None,
            },
            "network": {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
                "packets_sent": net.packets_sent,
                "packets_recv": net.packets_recv,
            },
            "gpu": None,
            "hardware_specs": hardware_specs,
            "workload_capacity": _workload_capacity_from_specs(hardware_specs),
            "runner_processes": [],
            "runner_capacity": {},
        }

    # On WSL, also report the Windows host disk (/mnt/c) where the VHDX lives.
    # The host disk is the binding constraint — if it fills up, WSL itself breaks.
    windows_disk = None
    wsl_host_path = Path("/mnt/c")
    if wsl_host_path.exists():
        try:
            wd = shutil.disk_usage(str(wsl_host_path))
            windows_disk = {
                "path": str(wsl_host_path),
                "total_gb": round(wd.total / (1024**3), 1),
                "used_gb": round(wd.used / (1024**3), 1),
                "free_gb": round(wd.free / (1024**3), 1),
                "percent": round(wd.used / wd.total * 100, 1),
                "pressure": _disk_pressure_snapshot(
                    path=str(wsl_host_path),
                    total_gb=round(wd.total / (1024**3), 1),
                    used_gb=round(wd.used / (1024**3), 1),
                    free_gb=round(wd.free / (1024**3), 1),
                    percent=round(wd.used / wd.total * 100, 1),
                ),
            }
        except OSError:
            pass

    # Use the more critical disk for the top-level pressure signal.
    windows_pct = (windows_disk or {}).get("percent", 0)
    effective_pct = max(disk_percent, windows_pct)
    effective_free = min(
        disk_free_gb,
        (windows_disk or {}).get("free_gb", disk_free_gb),
    )
    disk_pressure = _disk_pressure_snapshot(
        path=disk_path,
        total_gb=disk_total_gb,
        used_gb=disk_used_gb,
        free_gb=effective_free,
        percent=effective_pct,
    )

    # Load averages (1, 5, 15 min)
    try:
        load_avg = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
    except OSError:
        load_avg = (0, 0, 0)

    # Network I/O
    net = psutil.net_io_counters()

    # Per-CPU usage
    per_cpu = psutil.cpu_percent(interval=0, percpu=True)
    current_cpu = psutil.cpu_percent(interval=0)
    _cpu_history.append(current_cpu)
    cpu_avg_1m = round(sum(_cpu_history) / len(_cpu_history), 1) if _cpu_history else current_cpu

    # Uptime
    uptime_seconds = time.time() - psutil.boot_time()
    dashboard_uptime = time.time() - BOOT_TIME
    gpu = get_gpu_info()
    hardware_specs = _local_hardware_specs(gpu)

    return {
        "hostname": HOSTNAME,
        "platform": platform.platform(),
        "timestamp": datetime.now(UTC).isoformat(),
        "uptime_seconds": int(uptime_seconds),
        "dashboard_uptime_seconds": int(dashboard_uptime),
        "cpu": {
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(logical=True),
            "percent": current_cpu,
            "percent_1m_avg": cpu_avg_1m,
            "per_cpu_percent": per_cpu,
            "freq_current_mhz": round(cpu_freq.current, 0) if cpu_freq else None,
            "freq_max_mhz": round(cpu_freq.max, 0) if cpu_freq else None,
            "load_avg_1m": round(load_avg[0], 2),
            "load_avg_5m": round(load_avg[1], 2),
            "load_avg_15m": round(load_avg[2], 2),
        },
        "memory": {
            "host_total_gb": HOST_MEMORY_GB,
            "wsl_total_gb": round(mem.total / (1024**3), 1),
            "total_gb": HOST_MEMORY_GB or round(mem.total / (1024**3), 1),
            "used_gb": round(mem.used / (1024**3), 1),
            "available_gb": round(mem.available / (1024**3), 1),
            "percent": mem.percent,
            "swap_total_gb": round(swap.total / (1024**3), 1),
            "swap_used_gb": round(swap.used / (1024**3), 1),
            "swap_percent": swap.percent,
        },
        "disk": {
            "path": disk_path,
            "total_gb": disk_total_gb,
            "used_gb": disk_used_gb,
            "free_gb": disk_free_gb,
            "percent": disk_percent,
            "pressure": disk_pressure,
            "windows_host": windows_disk,
        },
        "network": {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "packets_sent": net.packets_sent,
            "packets_recv": net.packets_recv,
        },
        "gpu": gpu,
        "hardware_specs": hardware_specs,
        "workload_capacity": _workload_capacity_from_specs(hardware_specs),
        "runner_processes": get_per_runner_resources(),
        "runner_capacity": get_runner_capacity_snapshot(),
    }


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
