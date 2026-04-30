"""Fleet status and monitoring routes (read-only).

Read-only fleet orchestration endpoints: node aggregation, hardware specs,
deployment state tracking, and audit log retrieval. These routes collect
and surface system metrics from the fleet without making mutations.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dashboard_config import FLEET_NODES, HOSTNAME, MACHINE_ROLE, ORG, PORT
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_principal, require_scope
from machine_registry import load_machine_registry, merge_registry_with_live_nodes
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub
from system_utils import get_system_metrics_snapshot

import health as _health_router  # noqa: E402

UTC = timezone.utc  # noqa: UP017
log = logging.getLogger("dashboard.fleet_status")
router = APIRouter(tags=["fleet"])

# Module-level path for orchestration audit logs
_ORCHESTRATION_AUDIT_PATH = Path.home() / "actions-runners" / "dashboard" / "orchestration_audit.json"


def _node_deployment_info(node: dict) -> dict:
    """Return the deployment payload reported by a fleet node."""
    health = node.get("health") if isinstance(node.get("health"), dict) else {}
    deployment = health.get("deployment") if isinstance(health, dict) else {}
    if not isinstance(deployment, dict):
        deployment = {}
    payload = dict(deployment)
    payload.setdefault("app", "runner-dashboard")
    payload.setdefault("version", "unknown")
    payload.setdefault("git_sha", "unknown")
    payload.setdefault("git_branch", "unknown")
    return payload


def _deployment_info() -> dict:
    """Return this dashboard's deployment metadata from VERSION file."""
    try:
        version_file = Path(__file__).parent.parent.parent / "VERSION"
        version = version_file.read_text(encoding="utf-8").strip()
    except (OSError, FileNotFoundError):
        version = "unknown"
    return {
        "version": version,
        "timestamp": datetime.now(UTC).isoformat(),
        "dirty": False,
    }


def _classify_node_offline(exc: Exception | None = None, *, status_code: int | None = None) -> dict:
    """Classify why a fleet node is not fully reachable."""
    message = str(exc) if exc else ""
    lower = message.lower()
    if status_code is not None:
        return {
            "offline_reason": "dashboard_unhealthy",
            "offline_detail": f"Dashboard returned HTTP {status_code}",
        }
    if isinstance(exc, httpx.TimeoutException) or "timed out" in lower:
        return {
            "offline_reason": "computer_offline",
            "offline_detail": "Dashboard host timed out over the fleet network.",
        }
    if isinstance(exc, httpx.ConnectError):
        cause = exc.__cause__ or exc
        os_error = cause if isinstance(cause, OSError) else None
        if os_error and os_error.errno == errno.ECONNREFUSED:
            return {
                "offline_reason": "wsl_connection_lost",
                "offline_detail": (
                    "Host is reachable, but the dashboard port refused the connection. "
                    "WSL, systemd, or the dashboard service is likely stopped."
                ),
            }
        if os_error and os_error.errno in {
            errno.ENETUNREACH,
            errno.EHOSTUNREACH,
            errno.ECONNRESET,
        }:
            return {
                "offline_reason": "computer_offline",
                "offline_detail": "Fleet network could not reach the computer.",
            }
    if "connection refused" in lower:
        return {
            "offline_reason": "wsl_connection_lost",
            "offline_detail": "Dashboard port refused the connection.",
        }
    if "network is unreachable" in lower or "no route to host" in lower:
        return {
            "offline_reason": "computer_offline",
            "offline_detail": "Fleet network route to the computer is unavailable.",
        }
    return {
        "offline_reason": "unknown",
        "offline_detail": message or "Dashboard node is unreachable.",
    }


def _resource_offline_reason(system: dict) -> dict | None:
    """Return a resource-monitor reason when local metrics indicate throttling."""
    cpu = system.get("cpu") or {}
    memory = system.get("memory") or {}
    disk = system.get("disk") or {}
    pressure = []
    if (cpu.get("percent_1m_avg") or cpu.get("percent") or 0) >= 95:
        pressure.append("CPU >= 95%")
    if (memory.get("percent") or 0) >= 92:
        pressure.append("memory >= 92%")
    if (disk.get("pressure") or {}).get("status") == "critical":
        pressure.append("disk pressure critical")
    elif (disk.get("percent") or 0) >= 95:
        pressure.append("disk >= 95%")
    if not pressure:
        return None
    return {
        "offline_reason": "resource_monitoring",
        "offline_detail": "Resource pressure detected: " + ", ".join(pressure),
    }


def _node_visibility_snapshot(node: dict) -> dict:
    """Summarize how much useful telemetry a node currently exposes."""
    online = bool(node.get("online"))
    dashboard_reachable = node.get("dashboard_reachable") is not False
    has_system_metrics = bool(node.get("system"))
    resource_pressure = node.get("offline_reason") == "resource_monitoring"

    if resource_pressure:
        return {
            "visibility_state": "degraded",
            "visibility_label": "Degraded",
            "visibility_tone": "yellow",
            "visibility_detail": node.get("offline_detail") or "Resource pressure is high enough to warrant attention.",
        }

    if online and dashboard_reachable and has_system_metrics:
        return {
            "visibility_state": "full_telemetry",
            "visibility_label": "Full telemetry",
            "visibility_tone": "green",
            "visibility_detail": ("Runner status and system metrics are both available."),
        }

    if online:
        return {
            "visibility_state": "runners_only",
            "visibility_label": "Runners only",
            "visibility_tone": "orange",
            "visibility_detail": ("Runner registrations are healthy, but dashboard telemetry is unavailable."),
        }

    if dashboard_reachable:
        return {
            "visibility_state": "dashboard_only",
            "visibility_label": "Dashboard only",
            "visibility_tone": "blue",
            "visibility_detail": ("Dashboard is reachable, but runner registrations are offline."),
        }

    return {
        "visibility_state": "offline",
        "visibility_label": "Offline",
        "visibility_tone": "red",
        "visibility_detail": (node.get("offline_detail") or "The machine is not reachable over the fleet network."),
    }


def _machine_deployment_state(node: dict, expected_version: str) -> dict:
    """Build a per-machine deployment state record."""
    import deployment_drift

    deployment = _node_deployment_info(node)
    status = deployment_drift.evaluate_drift(deployment, expected_version)
    _reg = node.get("registry")
    registry = _reg if isinstance(_reg, dict) else {}
    _h = node.get("health")
    health = _h if isinstance(_h, dict) else {}
    last_health_check = health.get("timestamp") or node.get("last_seen")
    last_rollback = None
    if isinstance(registry, dict):
        deployment_meta = registry.get("deployment")
        if isinstance(deployment_meta, dict):
            last_rollback = deployment_meta.get("last_rollback")
        if last_rollback is None:
            maintenance = registry.get("maintenance")
            if isinstance(maintenance, dict):
                last_rollback = maintenance.get("last_rollback")
    if not node.get("online"):
        rollout_state = "offline"
        rollout_label = "Offline"
        rollout_detail = node.get("offline_detail") or node.get("error") or "Node is offline."
    elif status.dirty:
        rollout_state = "dirty"
        rollout_label = "Dirty"
        rollout_detail = "Node is running a dirty checkout and needs a clean redeploy."
    elif status.drift:
        rollout_state = "drifted"
        rollout_label = "Drifting"
        rollout_detail = status.message
    elif node.get("offline_reason") == "resource_monitoring":
        rollout_state = "degraded"
        rollout_label = "Degraded"
        rollout_detail = node.get("offline_detail") or "Resource pressure is blocking the usual rollout cadence."
    elif status.current == "unknown":
        rollout_state = "unknown"
        rollout_label = "Unknown"
        rollout_detail = "Deployment metadata is missing, so the node's rollout state cannot be compared."
    else:
        rollout_state = "steady"
        rollout_label = "In sync"
        rollout_detail = status.message

    return {
        "name": node.get("name"),
        "display_name": registry.get("display_name") or node.get("name"),
        "role": registry.get("role") or node.get("role"),
        "online": bool(node.get("online")),
        "dashboard_reachable": bool(node.get("dashboard_reachable")),
        "desired_version": expected_version,
        "deployed_version": status.current,
        "drift_status": status.to_dict(),
        "rollout_state": rollout_state,
        "rollout_label": rollout_label,
        "rollout_detail": rollout_detail,
        "last_health_check": last_health_check,
        "last_rollback": last_rollback,
        "update_available": status.drift and not status.dirty,
    }


def _build_deployment_state(nodes: list[dict], expected_version: str) -> dict:
    """Summarize deployment state across the fleet."""
    import deployment_drift

    deployment = _deployment_info()
    local_drift = deployment_drift.evaluate_drift(deployment, expected_version)
    machines = [_machine_deployment_state(node, expected_version) for node in nodes]
    attention_states = {"offline", "dirty", "drifted", "degraded", "unknown"}
    alerting = [machine for machine in machines if machine["rollout_state"] in attention_states]
    online = sum(1 for machine in machines if machine["online"])
    steady = sum(1 for machine in machines if machine["rollout_state"] == "steady")
    dirty = sum(1 for machine in machines if machine["rollout_state"] == "dirty")
    offline = sum(1 for machine in machines if machine["rollout_state"] == "offline")
    drifted = sum(1 for machine in machines if machine["rollout_state"] == "drifted")
    degraded = sum(1 for machine in machines if machine["rollout_state"] == "degraded")
    unknown = sum(1 for machine in machines if machine["rollout_state"] == "unknown")
    if not machines:
        rollout_status = "unknown"
    elif dirty:
        rollout_status = "blocked"
    elif offline or degraded:
        rollout_status = "degraded"
    elif drifted or unknown or alerting:
        rollout_status = "attention"
    else:
        rollout_status = "stable"
    summary = (
        f"{steady}/{len(machines)} machines are on {expected_version}"
        if machines
        else "No fleet machines reported deployment metadata."
    )
    if alerting:
        summary += f" {offline} offline, {drifted} drifting, {dirty} dirty, {degraded} degraded, {unknown} unknown."
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "deployment": deployment,
        "expected_version": expected_version,
        "drift": local_drift.to_dict(),
        "rollout_state": {
            "status": rollout_status,
            "summary": summary,
            "machines_total": len(machines),
            "machines_online": online,
            "machines_steady": steady,
            "machines_dirty": dirty,
            "machines_offline": offline,
            "machines_drifting": drifted,
            "machines_degraded": degraded,
            "machines_unknown": unknown,
            "machines_attention": len(alerting),
        },
        "machines": machines,
    }


async def _collect_live_fleet_nodes() -> list[dict]:
    """Collect the live fleet node payload before registry metadata is merged."""

    async def fetch_node(name: str, url: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                sys_r, health_r = await asyncio.gather(
                    client.get(f"{url}/api/system"),
                    client.get(f"{url}/api/health"),
                )
            if sys_r.status_code != 200 or health_r.status_code != 200:
                status_code = sys_r.status_code if sys_r.status_code != 200 else health_r.status_code
                reason = _classify_node_offline(status_code=status_code)
                return {
                    "name": name,
                    "url": url,
                    "online": False,
                    "dashboard_reachable": True,
                    "is_local": False,
                    "role": "node",
                    "system": sys_r.json() if sys_r.status_code == 200 else {},
                    "health": health_r.json() if health_r.status_code == 200 else {},
                    "last_seen": None,
                    "error": reason["offline_detail"],
                    **reason,
                }
            system = sys_r.json()
            resource_reason = _resource_offline_reason(system)
            return {
                "name": name,
                "url": url,
                "online": True,
                "dashboard_reachable": True,
                "is_local": False,
                "role": "node",
                "system": system,
                "hardware_specs": system.get("hardware_specs", {}),
                "workload_capacity": system.get("workload_capacity", {}),
                "health": health_r.json(),
                "last_seen": datetime.now(UTC).isoformat(),
                "error": None,
                "offline_reason": (resource_reason["offline_reason"] if resource_reason else None),
                "offline_detail": (resource_reason["offline_detail"] if resource_reason else None),
            }
        except Exception as exc:  # noqa: BLE001
            reason = _classify_node_offline(exc)
            return {
                "name": name,
                "url": url,
                "online": False,
                "dashboard_reachable": False,
                "is_local": False,
                "role": "node",
                "system": {},
                "health": {},
                "last_seen": None,
                "error": reason["offline_detail"],
                **reason,
            }

    local_sys = await get_system_metrics_snapshot()
    local_health = await _health_router._health_impl()
    local_resource_reason = _resource_offline_reason(local_sys)
    nodes: list[dict] = [
        {
            "name": HOSTNAME,
            "url": f"http://localhost:{PORT}",
            "online": True,
            "dashboard_reachable": True,
            "is_local": True,
            "role": MACHINE_ROLE,
            "system": local_sys,
            "hardware_specs": local_sys.get("hardware_specs", {}),
            "workload_capacity": local_sys.get("workload_capacity", {}),
            "health": local_health,
            "last_seen": datetime.now(UTC).isoformat(),
            "error": None,
            "offline_reason": (local_resource_reason["offline_reason"] if local_resource_reason else None),
            "offline_detail": (local_resource_reason["offline_detail"] if local_resource_reason else None),
        }
    ]

    if FLEET_NODES:
        remote = await asyncio.gather(*[fetch_node(name, url) for name, url in FLEET_NODES.items()])
        nodes.extend(remote)

    return nodes


async def _get_fleet_nodes_impl() -> dict:
    """Aggregate system metrics + health from all fleet nodes.

    Always includes this machine (no HTTP round-trip).  Remote nodes are
    queried concurrently over Tailscale using FLEET_NODES config.
    Offline nodes are included with online=False so the UI can show them.
    """
    nodes = await _collect_live_fleet_nodes()
    try:
        registry = load_machine_registry()
    except Exception as exc:  # noqa: BLE001
        log.warning("Machine registry load failed: %s", exc)
        registry = {"version": 1, "machines": []}
    nodes = merge_registry_with_live_nodes(nodes, registry)
    nodes = [{**node, **_node_visibility_snapshot(node)} for node in nodes]
    online = sum(1 for n in nodes if n["online"])
    total_runners = sum(n["health"].get("runners_registered", 0) for n in nodes)
    backend_dir = Path(__file__).parent.parent
    return {
        "nodes": nodes,
        "count": len(nodes),
        "online_count": online,
        "total_runners": total_runners,
        "registry": {
            "path": str(backend_dir / "machine_registry.yml"),
            "version": registry.get("version", 1),
            "machines": len(registry.get("machines", [])),
        },
    }


def _load_orchestration_audit(limit: int = 50, principal: str | None = None) -> list[dict]:
    """Load recent orchestration audit entries from disk."""
    if not _ORCHESTRATION_AUDIT_PATH.exists():
        return []
    try:
        raw = _ORCHESTRATION_AUDIT_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        entries = json.loads(raw)
        if isinstance(entries, list):
            if principal:
                entries = [e for e in entries if e.get("principal") == principal]
            return entries[-limit:]
        return []
    except (OSError, json.JSONDecodeError):
        return []


# ─── Routes ──────────────────────────────────────────────────────────────────


@router.get("/api/fleet/nodes")
async def get_fleet_nodes(request: Request) -> dict:
    """Get aggregated fleet nodes with system metrics and health status."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    return await _get_fleet_nodes_impl()


@router.get("/api/fleet/hardware")
async def get_fleet_hardware(request: Request) -> dict:
    """Return centralized fleet hardware specs for workload placement."""
    if should_proxy_fleet_to_hub(request):
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


@router.get("/api/fleet/nodes/{node_name}/system")
async def proxy_node_system(node_name: str) -> dict:
    """Proxy /api/system from a named fleet node (for detailed drill-down)."""
    if node_name in (HOSTNAME, "local"):
        return await get_system_metrics_snapshot()
    url = FLEET_NODES.get(node_name)
    if not url:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_name}")
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


@router.get("/api/fleet/orchestration")
async def get_fleet_orchestration(request: Request) -> dict:
    """Return per-machine job assignment, queue, and capacity for fleet orchestration view."""
    registry_data = load_machine_registry()
    machines_raw = registry_data.get("machines", [])

    # Try to enrich with live node data from cache
    try:
        fleet = await _get_fleet_nodes_impl()
        live_nodes = {n.get("name", ""): n for n in fleet.get("nodes", [])}
    except Exception:  # noqa: BLE001
        live_nodes = {}

    machines = []
    for m in machines_raw:
        name = m.get("name", "")
        live = live_nodes.get(name, {})
        online = bool(live.get("online", False)) if live else False
        system_info = live.get("system", {}) if live else {}
        runners_info = live.get("runners", []) if live else []
        runner_count = len(runners_info) if isinstance(runners_info, list) else 0
        busy_count = sum(1 for r in runners_info if r.get("busy")) if runner_count else 0
        machines.append(
            {
                "name": name,
                "display_name": m.get("display_name") or name,
                "role": m.get("role", "node"),
                "online": online,
                "runner_count": runner_count,
                "busy_runners": busy_count,
                "queue_depth": max(0, busy_count),
                "last_ping": live.get("last_ping") or live.get("checked_at"),
                "dashboard_url": m.get("dashboard_url"),
                "runner_labels": m.get("runner_labels", []),
                "offline_reason": live.get("offline_reason"),
                "cpu_percent": system_info.get("cpu_percent"),
                "memory_percent": system_info.get("memory_percent"),
            }
        )

    audit_entries = _load_orchestration_audit(limit=10)
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "machines": machines,
        "online_count": sum(1 for m in machines if m["online"]),
        "total_count": len(machines),
        "audit_log": list(reversed(audit_entries)),
    }


@router.get("/api/fleet/audit")
async def get_fleet_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),
) -> list[dict]:
    """Return this fleet's orchestration audit log."""
    return _load_orchestration_audit(limit=limit, principal=principal)


@router.get("/api/audit")
async def get_node_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),
) -> list[dict]:
    """Return this node's orchestration audit log."""
    return _load_orchestration_audit(limit=limit, principal=principal)
