"""Deployment state and drift routes.

Extracted from server.py (issue #357).
Routes: GET /api/deployment, GET /api/deployment/expected-version,
        GET /api/deployment/drift, GET /api/deployment/state,
        POST /api/deployment/update-signal, GET /api/deployment/git-drift.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import deployment_drift
from dashboard_config import EXPECTED_VERSION_FILE, HOSTNAME
from fastapi import APIRouter, Depends, Request
from identity import Principal, require_scope  # noqa: B008

if TYPE_CHECKING:
    from collections.abc import Callable

import datetime as _dt_mod

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017

log = logging.getLogger("dashboard.deployment")
router = APIRouter(tags=["deployment"])

# ---------------------------------------------------------------------------
# Injected dependencies (set by server.py after import)
# ---------------------------------------------------------------------------

_get_fleet_nodes_impl: Callable[[], Any] | None = None
_proxy_to_hub: Callable[[Request], Any] | None = None
_should_proxy_fleet_to_hub: Callable[[Request], bool] | None = None
_deployment_info: Callable[[], dict] | None = None
_read_expected_dashboard_version: Callable[[], Any] | None = None
_build_deployment_state: Callable[[list, str], dict] | None = None


def set_dependencies(
    get_fleet_nodes_impl: Callable,
    proxy_to_hub: Callable,
    should_proxy_fleet_to_hub: Callable,
    deployment_info: Callable,
    read_expected_dashboard_version: Callable,
    build_deployment_state: Callable,
) -> None:
    """Wire server.py helpers into this router (called at startup)."""
    global _get_fleet_nodes_impl, _proxy_to_hub, _should_proxy_fleet_to_hub  # noqa: PLW0603
    global _deployment_info, _read_expected_dashboard_version, _build_deployment_state  # noqa: PLW0603
    _get_fleet_nodes_impl = get_fleet_nodes_impl
    _proxy_to_hub = proxy_to_hub
    _should_proxy_fleet_to_hub = should_proxy_fleet_to_hub
    _deployment_info = deployment_info
    _read_expected_dashboard_version = read_expected_dashboard_version
    _build_deployment_state = build_deployment_state


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/deployment")
async def get_deployment() -> dict:
    """Return the dashboard code revision deployed on this machine."""
    return _deployment_info()  # type: ignore[misc]


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
    """Compare the deployed version against the hub's expected VERSION."""
    expected = await _read_expected_dashboard_version()  # type: ignore[misc]
    status = deployment_drift.evaluate_drift(_deployment_info(), expected)  # type: ignore[misc]
    return status.to_dict()


@router.get("/api/deployment/state")
async def get_deployment_state(request: Request) -> dict:
    """Return dashboard deployment state for the fleet overview and deployment tab."""
    if _should_proxy_fleet_to_hub(request):  # type: ignore[misc]
        return await _proxy_to_hub(request)  # type: ignore[misc]
    fleet = await _get_fleet_nodes_impl()  # type: ignore[misc]
    expected = await _read_expected_dashboard_version()  # type: ignore[misc]
    return _build_deployment_state(fleet.get("nodes", []), expected)  # type: ignore[misc]


@router.post("/api/deployment/update-signal")
async def post_deployment_update_signal(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("system.control")),  # noqa: B008
) -> dict:
    """Emit a structured "update requested" event for a node."""
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        payload = {}
    node = str(payload.get("node") or HOSTNAME)
    reason = str(payload.get("reason") or "user-requested")
    dry_run = bool(payload.get("dry_run", False))

    expected = await _read_expected_dashboard_version()  # type: ignore[misc]
    status = deployment_drift.evaluate_drift(_deployment_info(), expected)  # type: ignore[misc]
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


@router.get("/api/deployment/git-drift")
async def get_git_drift() -> dict:
    """Return git-commit-based drift: compares HEAD against origin/main."""
    repo_root = Path(__file__).parent.parent.parent
    result: dict[str, object] = {}

    source = "unknown"
    try:
        out = await asyncio.to_thread(
            subprocess.run,
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=repo_root,
        )
        source = out.stdout.strip()
        result["source_commit"] = source[:12]
    except Exception:  # noqa: BLE001
        result["source_commit"] = "unknown"

    try:
        out = await asyncio.to_thread(
            subprocess.run,
            ["git", "rev-parse", "origin/main"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=repo_root,
        )
        remote = out.stdout.strip()
        result["remote_commit"] = remote[:12]
        result["is_drifted"] = bool(source and remote and source != remote)
        if result["is_drifted"]:
            result["drift_details"] = "deployed version differs from origin/main"
        else:
            result["drift_details"] = "up to date"
    except Exception:  # noqa: BLE001
        result["is_drifted"] = False
        result["remote_commit"] = "unknown"
        result["drift_details"] = "could not reach origin/main"

    result["process_pid"] = os.getpid()
    return result
