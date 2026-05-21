"""Deployment state helpers — drift evaluation across fleet nodes.

Extracted from server.py (issue #2942).

Public API
----------
_node_deployment_info()               — extract deployment payload from a node dict
_machine_deployment_state()           — build per-machine rollout state record
build_deployment_state()              — summarize deployment state across the fleet
expected_dashboard_version_from_hub() — fetch hub's expected VERSION for spoke nodes
read_expected_dashboard_version()     — read expected VERSION (hub or local file)
"""

from __future__ import annotations

import datetime as _dt_mod
import json
import logging
import os
from pathlib import Path
from typing import Any

import deployment_drift
import httpx

log = logging.getLogger("dashboard")

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)
datetime = _dt_mod.datetime

# ---------------------------------------------------------------------------
# Module-level configuration (injected by server.py at startup)
# ---------------------------------------------------------------------------

_app_version: str = "4.0.0"
_deployment_file: Path = Path(__file__).resolve().parent.parent / "deployment.json"
_expected_version_file: Path = Path(__file__).resolve().parent.parent / "VERSION"
_machine_role: str = "node"
_hub_url: str | None = None
_hub_version_timeout: float = 5.0


def configure(
    *,
    app_version: str,
    deployment_file: Path,
    expected_version_file: Path | None = None,
    machine_role: str = "node",
    hub_url: str | None = None,
    hub_version_timeout: float = 5.0,
) -> None:
    """Inject runtime configuration from server.py."""
    global _app_version, _deployment_file, _expected_version_file  # noqa: PLW0603
    global _machine_role, _hub_url, _hub_version_timeout  # noqa: PLW0603
    _app_version = app_version
    _deployment_file = deployment_file
    if expected_version_file is not None:
        _expected_version_file = expected_version_file
    _machine_role = machine_role
    _hub_url = hub_url
    _hub_version_timeout = hub_version_timeout


# ---------------------------------------------------------------------------
# Deployment info
# ---------------------------------------------------------------------------


def deployment_info() -> dict:
    """Return the deployed dashboard revision recorded by update-deployed.sh.

    Postcondition: result is a dict with 'app' and 'version' keys.
    """
    fallback = {
        "app": "runner-dashboard",
        "version": _app_version,
        "git_sha": os.environ.get("DASHBOARD_GIT_SHA", "unknown"),
        "git_branch": os.environ.get("DASHBOARD_GIT_BRANCH", "unknown"),
        "source": "environment",
    }
    try:
        payload = json.loads(_deployment_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback
    if not isinstance(payload, dict):
        return fallback
    payload.setdefault("app", "runner-dashboard")
    payload.setdefault("version", _app_version)
    payload.setdefault("source", "deployment-file")
    assert "app" in payload and "version" in payload
    return payload


# ---------------------------------------------------------------------------
# Per-node deployment state
# ---------------------------------------------------------------------------


def _node_deployment_info(node: dict) -> dict:
    """Return the deployment payload reported by a fleet node.

    Postcondition: result has 'app', 'version', 'git_sha', 'git_branch' keys.
    """
    health = node.get("health") if isinstance(node.get("health"), dict) else {}
    dep = health.get("deployment") if isinstance(health, dict) else {}
    if not isinstance(dep, dict):
        dep = {}
    payload: dict[str, Any] = dict(dep)
    payload.setdefault("app", "runner-dashboard")
    payload.setdefault("version", "unknown")
    payload.setdefault("git_sha", "unknown")
    payload.setdefault("git_branch", "unknown")
    return payload


def _machine_deployment_state(node: dict, expected_version: str) -> dict:
    """Build a per-machine deployment state record.

    Precondition: node is a dict, expected_version is a non-empty string.
    Postcondition: result has 'rollout_state', 'rollout_label', 'rollout_detail'.
    """
    assert isinstance(expected_version, str) and expected_version, "expected_version must be non-empty"
    dep = _node_deployment_info(node)
    status = deployment_drift.evaluate_drift(dep, expected_version)
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
        rollout_state, rollout_label = "offline", "Offline"
        rollout_detail = node.get("offline_detail") or node.get("error") or "Node is offline."
    elif status.dirty:
        rollout_state, rollout_label = "dirty", "Dirty"
        rollout_detail = "Node is running a dirty checkout and needs a clean redeploy."
    elif status.drift:
        rollout_state, rollout_label = "drifted", "Drifting"
        rollout_detail = status.message
    elif node.get("offline_reason") == "resource_monitoring":
        rollout_state, rollout_label = "degraded", "Degraded"
        rollout_detail = node.get("offline_detail") or "Resource pressure is blocking the usual rollout cadence."
    elif status.current == "unknown":
        rollout_state, rollout_label = "unknown", "Unknown"
        rollout_detail = "Deployment metadata is missing, so the node's rollout state cannot be compared."
    else:
        rollout_state, rollout_label = "steady", "In sync"
        rollout_detail = status.message

    result = {
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
    assert "rollout_state" in result
    return result


def build_deployment_state(
    nodes: list[dict],
    expected_version: str,
) -> dict:
    """Summarize deployment state across the fleet.

    Precondition: nodes is a list, expected_version is a string.
    Postcondition: result has 'rollout_state', 'machines', 'deployment' keys.
    """
    assert isinstance(nodes, list), "nodes must be a list"
    dep = deployment_info()
    local_drift = deployment_drift.evaluate_drift(dep, expected_version)
    machines = [_machine_deployment_state(node, expected_version) for node in nodes]
    attention_states = {"offline", "dirty", "drifted", "degraded", "unknown"}
    alerting = [m for m in machines if m["rollout_state"] in attention_states]
    online = sum(1 for m in machines if m["online"])
    steady = sum(1 for m in machines if m["rollout_state"] == "steady")
    dirty = sum(1 for m in machines if m["rollout_state"] == "dirty")
    offline = sum(1 for m in machines if m["rollout_state"] == "offline")
    drifted = sum(1 for m in machines if m["rollout_state"] == "drifted")
    degraded = sum(1 for m in machines if m["rollout_state"] == "degraded")
    unknown_count = sum(1 for m in machines if m["rollout_state"] == "unknown")
    if not machines:
        rollout_status = "unknown"
    elif dirty:
        rollout_status = "blocked"
    elif offline or degraded:
        rollout_status = "degraded"
    elif drifted or unknown_count or alerting:
        rollout_status = "attention"
    else:
        rollout_status = "stable"
    summary = (
        f"{steady}/{len(machines)} machines are on {expected_version}"
        if machines
        else "No fleet machines reported deployment metadata."
    )
    if alerting:
        summary += (
            f" {offline} offline, {drifted} drifting, {dirty} dirty,"
            f" {degraded} degraded, {unknown_count} unknown."
        )
    result = {
        "timestamp": datetime.now(UTC).isoformat(),
        "deployment": dep,
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
            "machines_unknown": unknown_count,
            "machines_attention": len(alerting),
        },
        "machines": machines,
    }
    assert "rollout_state" in result and "machines" in result
    return result


# ---------------------------------------------------------------------------
# Expected-version fetch helpers (extracted from server.py issue #2942)
# ---------------------------------------------------------------------------


async def expected_dashboard_version_from_hub() -> str | None:
    """Fetch the hub's expected dashboard VERSION when this node is a spoke.

    Precondition: none (returns None if not a spoke or hub unreachable).
    Postcondition: returns a non-empty version string or None.
    """
    if _machine_role != "node" or not _hub_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=_hub_version_timeout) as client:
            response = await client.get(f"{_hub_url}/api/deployment/expected-version")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("hub expected-version fetch failed: %s", exc)
        return None
    expected = str(payload.get("expected") or "").strip()
    if not expected or expected == "unknown":
        return None
    return expected


async def read_expected_dashboard_version() -> str:
    """Return the hub's expected VERSION, falling back to the local checkout.

    Postcondition: always returns a non-empty string.
    """
    hub_ver = await expected_dashboard_version_from_hub()
    return hub_ver or deployment_drift.read_expected_version(_expected_version_file)
