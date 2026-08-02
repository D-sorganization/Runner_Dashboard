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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import deployment_drift
import proxy_utils
from dashboard_config import EXPECTED_VERSION_FILE, HOSTNAME
from fastapi import APIRouter, Depends, Request
from identity import Principal, require_fleet_peer, require_scope  # noqa: B008

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI

import datetime as _dt_mod

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017

log = logging.getLogger("dashboard.deployment")
router = APIRouter(tags=["deployment"])


@dataclass(frozen=True, slots=True)
class DeploymentDeps:
    get_fleet_nodes_impl: Callable[[], Awaitable[dict[str, Any]]]
    deployment_info: Callable[[], dict[str, Any]]
    read_expected_dashboard_version: Callable[[], Awaitable[str]]
    build_deployment_state: Callable[[list[Any], str], dict[str, Any]]


def set_dependencies(
    app: FastAPI,
    get_fleet_nodes_impl: Callable,
    deployment_info: Callable,
    read_expected_dashboard_version: Callable,
    build_deployment_state: Callable,
) -> None:
    """Wire server.py helpers into this router through FastAPI app state."""
    app.state.deployment_deps = DeploymentDeps(
        get_fleet_nodes_impl=get_fleet_nodes_impl,
        deployment_info=deployment_info,
        read_expected_dashboard_version=read_expected_dashboard_version,
        build_deployment_state=build_deployment_state,
    )


def deployment_deps(request: Request) -> DeploymentDeps:
    deps = getattr(request.app.state, "deployment_deps", None)
    if not isinstance(deps, DeploymentDeps):
        raise RuntimeError("deployment router dependencies are not configured")
    return deps


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/deployment")
async def get_deployment(deps: DeploymentDeps = Depends(deployment_deps)) -> dict:  # noqa: B008
    """Return the dashboard code revision deployed on this machine."""
    return deps.deployment_info()


@router.get("/api/deployment/expected-version")
async def get_expected_deployment_version() -> dict:
    """Return the local expected dashboard version for hub-spoke nodes."""
    return {
        "expected": deployment_drift.read_expected_version(EXPECTED_VERSION_FILE),
        "source": "local-version-file",
        "path": str(EXPECTED_VERSION_FILE),
    }


@router.get("/api/deployment/drift")
async def get_deployment_drift(deps: DeploymentDeps = Depends(deployment_deps)) -> dict:  # noqa: B008
    """Compare the deployed version against the hub's expected VERSION."""
    expected = await deps.read_expected_dashboard_version()
    status = deployment_drift.evaluate_drift(deps.deployment_info(), expected)
    return status.to_dict()


@router.get("/api/deployment/state", dependencies=[Depends(require_fleet_peer)])
async def get_deployment_state(
    request: Request,
    deps: DeploymentDeps = Depends(deployment_deps),  # noqa: B008
) -> dict:
    """Return dashboard deployment state for the fleet overview and deployment tab."""
    if proxy_utils.should_proxy_fleet_to_hub(request):
        return await proxy_utils.proxy_to_hub(request)
    fleet = await deps.get_fleet_nodes_impl()
    expected = await deps.read_expected_dashboard_version()
    return deps.build_deployment_state(fleet.get("nodes", []), expected)


@router.post("/api/deployment/update-signal")
async def post_deployment_update_signal(
    request: Request,
    *,
    deps: DeploymentDeps = Depends(deployment_deps),  # noqa: B008
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

    expected = await deps.read_expected_dashboard_version()
    status = deployment_drift.evaluate_drift(deps.deployment_info(), expected)
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
    except Exception as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
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
    except Exception as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        result["is_drifted"] = False
        result["remote_commit"] = "unknown"
        result["drift_details"] = "could not reach origin/main"

    result["process_pid"] = os.getpid()
    return result
