"""Fleet orchestration routes and helpers.

Handles:
- Fleet node discovery and health monitoring
- Deployment state tracking across the fleet
- Machine registry operations
- Fleet node visibility and classification
- Fleet control operations (runner scaling)
- Runner scheduling and capacity management
- Fleet orchestration (workflow dispatch, deployment actions)
- Audit logging for fleet operations
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_principal, require_scope

# Ensure backend directory is in path for module imports
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Import internal modules
import config_schema as config_schema
import deployment_drift as deployment_drift
import dispatch_contract as dispatch_contract
from dashboard_config import (
    BACKEND_DIR as CONFIG_BACKEND_DIR,
    FLEET_NODES,
    HUB_URL,
    HOSTNAME,
    MACHINE_ROLE,
    MAX_RUNNERS,
    NUM_RUNNERS,
    ORG,
    PORT,
    RUNNER_BASE_DIR,
)
from machine_registry import load_machine_registry, merge_registry_with_live_nodes
from system_utils import (
    classify_node_offline,
    get_system_metrics_snapshot,
    resource_offline_reason,
)
from utils.utilities import (
    _should_proxy_fleet_to_hub as util_should_proxy_fleet_to_hub,
    gh_api_admin,
    proxy_to_hub as util_proxy_to_hub,
    run_cmd,
    safe_subprocess_env,
    sanitize_log_value,
)

UTC = timezone.utc  # noqa: UP017
log = logging.getLogger("dashboard.fleet")
router = APIRouter(tags=["fleet"])

# ─── Constants ────────────────────────────────────────────────────────────────

DEPLOYMENT_FILE = Path.home() / "actions-runners" / "dashboard" / "deployment.json"
EXPECTED_VERSION_FILE = Path.home() / "actions-runners" / "dashboard" / "VERSION"

_ORCHESTRATION_AUDIT_PATH = Path.home() / "actions-runners" / "dashboard" / "orchestration_audit.json"
_orchestration_audit_lock: asyncio.Lock = asyncio.Lock()
_DEPLOY_ACTIONS = {"sync_workflows", "restart_runner", "update_config"}


# ─── Wrapper functions ────────────────────────────────────────────────────────


async def proxy_to_hub(request: Request) -> dict:
    """Proxy request to the designated HUB_URL for hub-spoke topology."""
    return await util_proxy_to_hub(request, HUB_URL)


def _should_proxy_fleet_to_hub(request: Request) -> bool:
    """Return True when this node should use the hub's fleet-wide view."""
    return util_should_proxy_fleet_to_hub(request, MACHINE_ROLE, HUB_URL)


# ─── Helper Functions ─────────────────────────────────────────────────────────


def _deployment_info() -> dict:
    """Return the deployed dashboard revision recorded by update-deployed.sh."""
    fallback = {
        "app": "runner-dashboard",
        "version": "0.0.0",
        "git_sha": os.environ.get("DASHBOARD_GIT_SHA", "unknown"),
        "git_branch": os.environ.get("DASHBOARD_GIT_BRANCH", "unknown"),
        "source": "environment",
    }
    try:
        payload = json.loads(DEPLOYMENT_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback
    if not isinstance(payload, dict):
        return fallback
    payload.setdefault("app", "runner-dashboard")
    payload.setdefault("version", "0.0.0")
    payload.setdefault("source", "deployment-file")
    return payload


async def _expected_dashboard_version_from_hub() -> str | None:
    """Fetch the hub's expected dashboard VERSION when this node has a hub."""
    if MACHINE_ROLE != "node" or not HUB_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{HUB_URL}/api/deployment/expected-version")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("hub expected-version fetch failed: %s", exc)
        return None
    expected = str(payload.get("expected") or "").strip()
    if not expected or expected == "unknown":
        return None
    return expected


async def _read_expected_dashboard_version() -> str:
    """Return the hub expected VERSION, falling back to this checkout."""
    hub_version = await _expected_dashboard_version_from_hub()
    return hub_version or deployment_drift.read_expected_version(EXPECTED_VERSION_FILE)


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


def _machine_deployment_state(node: dict, expected_version: str) -> dict:
    """Build a per-machine deployment state record."""
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
        "visibility_detail": node.get("offline_detail") or node.get("error") or "No live telemetry from this machine.",
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
                reason = classify_node_offline(status_code=status_code)
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
            res_reason = resource_offline_reason(system)
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
                "offline_reason": (res_reason["offline_reason"] if res_reason else None),
                "offline_detail": (res_reason["offline_detail"] if res_reason else None),
            }
        except Exception as exc:  # noqa: BLE001
            reason = classify_node_offline(exc)
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
    local_res_reason = resource_offline_reason(local_sys)

    # Get local health data - make a local HTTP call to /api/health
    local_health = {}
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            health_r = await client.get(f"http://localhost:{PORT}/api/health")
            if health_r.status_code == 200:
                local_health = health_r.json()
    except Exception:  # noqa: BLE001
        local_health = {}

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
            "offline_reason": (local_res_reason["offline_reason"] if local_res_reason else None),
            "offline_detail": (local_res_reason["offline_detail"] if local_res_reason else None),
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
    return {
        "nodes": nodes,
        "count": len(nodes),
        "online_count": online,
        "total_runners": total_runners,
        "registry": {
            "path": str(BACKEND_DIR / "machine_registry.yml"),
            "version": registry.get("version", 1),
            "machines": len(registry.get("machines", [])),
        },
    }


async def _fleet_control_local(action: str) -> dict:
    """Scale runners on this machine only."""
    data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
    runners = data.get("runners", [])
    results = []

    log.info("Local runner control on %s: %s", HOSTNAME, action)

    if action == "all-up":
        for i in range(1, max(NUM_RUNNERS, MAX_RUNNERS) + 1):
            svc = RUNNER_BASE_DIR / f"runner-{i}" / "svc.sh"
            if svc.exists():
                code, _, _ = await run_cmd(["sudo", str(svc), "start"], timeout=30)
                results.append({"runner": i, "action": "start", "success": code == 0})

    elif action == "all-down":
        for i in range(1, max(NUM_RUNNERS, MAX_RUNNERS) + 1):
            svc = RUNNER_BASE_DIR / f"runner-{i}" / "svc.sh"
            if svc.exists():
                code, _, _ = await run_cmd(["sudo", str(svc), "stop"], timeout=30)
                results.append({"runner": i, "action": "stop", "success": code == 0})

    elif action == "up":
        online_nums = set()
        for r in runners:
            if r["status"] == "online":
                name = r.get("name", "")
                parts = name.rsplit("-", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    online_nums.add(int(parts[1]))
        for i in range(1, max(NUM_RUNNERS, MAX_RUNNERS) + 1):
            if i not in online_nums:
                svc = RUNNER_BASE_DIR / f"runner-{i}" / "svc.sh"
                if svc.exists():
                    code, _, _ = await run_cmd(["sudo", str(svc), "start"], timeout=30)
                    results.append({"runner": i, "action": "start", "success": code == 0})
                    break

    elif action == "down":
        idle_runners = []
        for r in runners:
            if r["status"] == "online" and not r.get("busy"):
                name = r.get("name", "")
                parts = name.rsplit("-", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    idle_runners.append(int(parts[1]))
        if idle_runners:
            target = max(idle_runners)
            svc = RUNNER_BASE_DIR / f"runner-{target}" / "svc.sh"
            if svc.exists():
                code, _, _ = await run_cmd(["sudo", str(svc), "stop"], timeout=30)
                results.append({"runner": target, "action": "stop", "success": code == 0})
        else:
            raise HTTPException(status_code=400, detail="No idle runners to stop")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    return {"machine": HOSTNAME, "action": action, "results": results}


async def _remote_fleet_control(name: str, url: str, action: str) -> dict:
    """Ask a node dashboard to apply a runner action locally."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(f"{url}/api/fleet/control/{action}?local=1")
        if resp.status_code != 200:
            return {
                "machine": name,
                "url": url,
                "success": False,
                "status_code": resp.status_code,
                "error": resp.text[:500],
            }
        data = resp.json()
        return {
            "machine": name,
            "url": url,
            "success": True,
            "result": data,
        }
    except Exception as exc:  # noqa: BLE001 - remote nodes may be offline
        return {"machine": name, "url": url, "success": False, "error": str(exc)}


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


async def _append_orchestration_audit(entry: dict) -> None:
    """Append a single audit entry to the orchestration audit log (thread-safe)."""
    async with _orchestration_audit_lock:
        existing = _load_orchestration_audit(limit=1000)
        existing.append(entry)
        try:
            config_schema.atomic_write_json(_ORCHESTRATION_AUDIT_PATH, existing)
        except OSError as exc:
            log.warning("orchestration audit write failed: %s", exc)


# ─── API Routes ───────────────────────────────────────────────────────────────


@router.get("/api/deployment")
async def get_deployment() -> dict:
    """Return the dashboard code revision deployed on this machine."""
    return _deployment_info()


@router.get("/api/deployment/expected-version")
async def get_expected_deployment_version() -> dict:
    """Return the local expected dashboard version for hub-spoke nodes."""
    return {
        "expected": deployment_drift.read_expected_version(EXPECTED_VERSION_FILE),
        "source": "local-version-file",
        "path": str(EXPECTED_VERSION_FILE),
    }


@router.get("/api/deployment/drift")
async def get_deployment_drift() -> dict:
    """Compare the deployed version against the hub's expected VERSION.

    Used by the Machines tab to surface "Update available" badges on stale
    nodes. Remote update orchestration is intentionally out of scope here —
    see ``POST /api/deployment/update-signal`` for the notify-only affordance.
    """
    expected = await _read_expected_dashboard_version()
    status = deployment_drift.evaluate_drift(_deployment_info(), expected)
    return status.to_dict()


@router.get("/api/deployment/state")
async def get_deployment_state(request: Request) -> dict:
    """Return dashboard deployment state for the fleet overview and deployment tab."""
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    fleet = await _get_fleet_nodes_impl()
    expected = await _read_expected_dashboard_version()
    return _build_deployment_state(fleet.get("nodes", []), expected)


@router.post("/api/deployment/update-signal")
async def post_deployment_update_signal(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("system.control")),  # noqa: B008
) -> dict:
    """Emit a structured "update requested" event for a node.

    The dashboard UI calls this when an operator clicks the "Update node"
    affordance on a drifting machine card. We intentionally do *not* SSH
    or run ansible from here: this just logs a well-shaped event that
    ``scheduled-dashboard-maintenance.sh`` (or a future webhook consumer)
    can pick up. Callers should treat this as fire-and-notify only.
    """
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        payload = {}
    node = str(payload.get("node") or HOSTNAME)
    reason = str(payload.get("reason") or "user-requested")
    dry_run = bool(payload.get("dry_run", False))

    expected = await _read_expected_dashboard_version()
    status = deployment_drift.evaluate_drift(_deployment_info(), expected)
    if dry_run:
        preview = {
            "event": "dashboard.node.update_requested",
            "node": node,
            "current": status.current,
            "expected": status.expected,
            "severity": status.severity,
            "reason": reason,
            "dirty": status.dirty,
            "dry_run": True,
        }
        return {
            "accepted": True,
            "dry_run": True,
            "preview": preview,
            "drift": status.to_dict(),
        }
    event = deployment_drift.emit_update_signal(node, status, reason=reason)
    return {"accepted": True, "event": event, "drift": status.to_dict()}


@router.post("/api/fleet/control/{action}")
async def fleet_control(
    action: str,
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
):  # noqa: B008
    """Scale runners from any dashboard.

    Nodes proxy fleet-wide requests to the hub. The hub applies the action
    locally and fans it out to configured nodes. Internal fan-out calls use
    ``?local=1`` so each node controls its own runner services.
    """
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    scope = request.query_params.get("scope", "fleet")
    should_fan_out = MACHINE_ROLE == "hub" and scope != "local" and bool(FLEET_NODES)
    local_machine = HOSTNAME
    try:
        local_result = await _fleet_control_local(action)
        local_machine = local_result.get("machine", HOSTNAME)
        local_node_result = {
            "machine": local_machine,
            "url": f"http://localhost:{PORT}",
            "success": True,
            "result": local_result,
        }
    except HTTPException as exc:
        if not should_fan_out:
            raise
        local_result = {"machine": HOSTNAME, "action": action, "results": []}
        local_node_result = {
            "machine": HOSTNAME,
            "url": f"http://localhost:{PORT}",
            "success": False,
            "status_code": exc.status_code,
            "error": str(exc.detail),
        }
    node_results = [local_node_result]

    if should_fan_out:
        remotes = await asyncio.gather(*[_remote_fleet_control(name, url, action) for name, url in FLEET_NODES.items()])
        node_results.extend(remotes)

    return {
        "action": action,
        "scope": "local" if scope == "local" else "fleet",
        "machine": local_machine,
        "results": local_result["results"],
        "nodes": node_results,
    }


@router.get("/api/fleet/nodes")
async def get_fleet_nodes(request: Request) -> dict:
    """Get all nodes in the fleet with their current status and metrics."""
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    return await _get_fleet_nodes_impl()


@router.get("/api/fleet/hardware")
async def get_fleet_hardware(request: Request) -> dict:
    """Return centralized fleet hardware specs for workload placement."""
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


@router.post("/api/fleet/orchestration/dispatch")
async def fleet_orchestration_dispatch(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict:
    """Dispatch a workflow to a specific machine target."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Expected JSON object")

    repo = str(body.get("repo", "")).strip()
    workflow = str(body.get("workflow", "")).strip()
    ref = str(body.get("ref", "main")).strip() or "main"
    machine_target = str(body.get("machine_target", "")).strip()
    inputs = body.get("inputs") or {}
    approved_by = principal.id

    if not repo or not workflow:
        raise HTTPException(status_code=422, detail="repo and workflow are required")

    log.info(
        "audit: fleet_orchestration_dispatch repo=%s workflow=%s ref=%s target=%s by=%s",
        sanitize_log_value(repo),
        sanitize_log_value(workflow),
        sanitize_log_value(ref),
        sanitize_log_value(machine_target),
        sanitize_log_value(approved_by),
    )

    audit_id = uuid4().hex
    now_str = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    # Build a dispatch_contract envelope for auditing
    try:
        confirmation = dispatch_contract.DispatchConfirmation(
            approved_by=approved_by,
            approved_at=now_str,
            note=f"Fleet orchestration dispatch to {machine_target or 'any'}",
        )
        envelope = dispatch_contract.build_envelope(
            action="runner.status",  # read-only, used for audit record only
            source="fleet-orchestration",
            target=machine_target or "fleet",
            requested_by=approved_by,
            reason=f"Dispatch {workflow} on {repo}@{ref}",
            payload={"repo": repo, "workflow": workflow, "ref": ref, "inputs": inputs},
            confirmation=confirmation,
            principal=principal.id,
            on_behalf_of=getattr(request.state, "on_behalf_of", None) or "",
        )
        validation = dispatch_contract.validate_envelope(envelope)
        audit_entry_obj = dispatch_contract.build_audit_log_entry(envelope, validation)
        audit_entry = audit_entry_obj.to_dict()
    except Exception as exc:  # noqa: BLE001
        log.warning("orchestration dispatch audit build failed: %s", exc)
        audit_entry = {
            "event_id": audit_id,
            "action": "workflow.dispatch",
            "target": machine_target,
            "requested_by": approved_by,
            "decision": "accepted",
            "recorded_at": now_str,
        }

    audit_entry["orchestration_type"] = "workflow_dispatch"
    audit_entry["repo"] = repo
    audit_entry["workflow"] = workflow
    audit_entry["ref"] = ref
    audit_entry["machine_target"] = machine_target
    audit_entry["audit_id"] = audit_id
    await _append_orchestration_audit(audit_entry)

    log.info(
        "fleet-orchestration dispatch repo=%s workflow=%s ref=%s target=%s by=%s",
        sanitize_log_value(repo),
        sanitize_log_value(workflow),
        sanitize_log_value(ref),
        sanitize_log_value(machine_target),
        sanitize_log_value(approved_by),
    )

    # Attempt actual workflow dispatch via gh CLI
    run_url = None
    try:
        endpoint = f"/repos/{ORG}/{repo}/actions/workflows/{workflow}/dispatches"
        dispatch_payload: dict = {"ref": ref}
        if inputs:
            dispatch_payload["inputs"] = inputs
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as pf_obj:
            json.dump(dispatch_payload, pf_obj)
            pf = pf_obj.name
        try:
            code, _, stderr = await run_cmd(
                ["gh", "api", endpoint, "--method", "POST", "--input", pf],
                timeout=30,
                cwd=BACKEND_DIR.parent,
            )
        finally:
            with contextlib.suppress(OSError):
                Path(pf).unlink()
        if code != 0:
            log.warning("orchestration workflow dispatch gh failed: %s", stderr[:200])
    except Exception as exc:  # noqa: BLE001
        log.warning("orchestration dispatch gh call failed: %s", exc)

    return {
        "dispatched": True,
        "run_url": run_url,
        "audit_id": audit_id,
        "machine_target": machine_target,
        "repo": repo,
        "workflow": workflow,
        "ref": ref,
    }


@router.post("/api/fleet/orchestration/deploy")
async def fleet_orchestration_deploy(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict:
    """Deploy a workflow or config change to a fleet machine."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Expected JSON object")

    machine = str(body.get("machine", "")).strip()
    action = str(body.get("action", "")).strip()
    confirmed = bool(body.get("confirmed", False))
    requested_by = principal.id

    if not machine:
        raise HTTPException(status_code=422, detail="machine is required")
    if action not in _DEPLOY_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"action must be one of: {', '.join(sorted(_DEPLOY_ACTIONS))}",
        )
    if not confirmed:
        raise HTTPException(
            status_code=403,
            detail="confirmed=true is required to deploy to a fleet machine",
        )

    log.info(
        "audit: fleet_orchestration_deploy machine=%s action=%s by=%s",
        sanitize_log_value(machine),
        sanitize_log_value(action),
        sanitize_log_value(requested_by),
    )

    audit_id = uuid4().hex
    now_str = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    # Map deploy actions to dispatch_contract actions for auditing
    contract_action_map = {
        "sync_workflows": "dashboard.update_and_restart",
        "restart_runner": "runner.restart",
        "update_config": "runner.restart",
    }
    contract_action = contract_action_map.get(action, "runner.restart")

    try:
        confirmation = dispatch_contract.DispatchConfirmation(
            approved_by=requested_by,
            approved_at=now_str,
            note=f"Fleet deploy action={action} to machine={machine}",
        )
        envelope = dispatch_contract.build_envelope(
            action=contract_action,
            source="fleet-orchestration",
            target=machine,
            requested_by=requested_by,
            reason=f"Deploy action {action} to {machine}",
            payload={"deploy_action": action},
            confirmation=confirmation,
            principal=principal.id,
            on_behalf_of=getattr(request.state, "on_behalf_of", None) or "",
        )
        validation = dispatch_contract.validate_envelope(envelope)
        audit_entry_obj = dispatch_contract.build_audit_log_entry(envelope, validation)
        audit_entry = audit_entry_obj.to_dict()
    except Exception as exc:  # noqa: BLE001
        log.warning("orchestration deploy audit build failed: %s", exc)
        audit_entry = {
            "event_id": audit_id,
            "action": action,
            "target": machine,
            "requested_by": requested_by,
            "decision": "accepted",
            "recorded_at": now_str,
        }

    audit_entry["orchestration_type"] = "fleet_deploy"
    audit_entry["deploy_action"] = action
    audit_entry["machine"] = machine
    audit_entry["audit_id"] = audit_id
    await _append_orchestration_audit(audit_entry)

    log.info(
        "fleet-orchestration deploy machine=%s action=%s by=%s",
        sanitize_log_value(machine),
        sanitize_log_value(action),
        sanitize_log_value(requested_by),
    )

    action_labels = {
        "sync_workflows": "Sync workflows",
        "restart_runner": "Restart runner",
        "update_config": "Update config",
    }
    return {
        "deployed": True,
        "machine": machine,
        "action": action,
        "message": f"{action_labels.get(action, action)} dispatched to {machine}",
        "audit_id": audit_id,
    }


@router.get("/api/audit", tags=["fleet"])
async def get_node_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),
) -> list[dict]:
    """Return this node's orchestration audit log."""
    return _load_orchestration_audit(limit=limit, principal=principal)


@router.get("/api/fleet/audit", tags=["fleet"])
async def get_fleet_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),
) -> dict:
    """Return a merged view of orchestration audit logs across the fleet."""
    local_entries = _load_orchestration_audit(limit=limit, principal=principal)
    all_entries = list(local_entries)

    async def fetch_remote_audit(name: str, url: str) -> list[dict]:
        try:
            params: dict[str, Any] = {"limit": limit}
            if principal:
                params["principal"] = principal
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {}
                if auth_header := request.headers.get("Authorization"):
                    headers["Authorization"] = auth_header
                r = await client.get(f"{url}/api/audit", params=params, headers=headers)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to fetch audit from %s (%s): %s", name, url, exc)
        return []

    if FLEET_NODES:
        remotes = await asyncio.gather(*[fetch_remote_audit(n, u) for n, u in FLEET_NODES.items()])
        for r_entries in remotes:
            all_entries.extend(r_entries)

    def _parse_ts(entry: dict) -> datetime:
        ts_str = entry.get("timestamp") or entry.get("ts") or ""
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)

    all_entries.sort(key=_parse_ts, reverse=True)

    return {
        "entries": all_entries[:limit],
        "count": len(all_entries[:limit]),
    }
