"""Deployment status and update signaling routes."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_scope  # noqa: B008

import deployment_drift

log = logging.getLogger("dashboard")
router = APIRouter()


def _deployment_info() -> dict[str, Any]:
    """Return current deployment info (stub)."""
    return {"revision": "unknown", "deployed_at": None}


@router.get("/api/deployment")
async def get_deployment() -> dict:
    """Return the dashboard code revision deployed on this machine."""
    return _deployment_info()


@router.get("/api/deployment/expected-version")
async def get_expected_deployment_version() -> dict:
    """Return the local expected dashboard version for hub-spoke nodes."""
    from pathlib import Path
    EXPECTED_VERSION_FILE = Path(__file__).parent.parent / "EXPECTED_VERSION"
    return {
        "expected": deployment_drift.read_expected_version(EXPECTED_VERSION_FILE),
        "source": "local-version-file",
        "path": str(EXPECTED_VERSION_FILE),
    }


@router.get("/api/deployment/drift")
async def get_deployment_drift() -> dict:
    """Compare deployed version against expected."""
    expected = {}
    status = deployment_drift.evaluate_drift(_deployment_info(), expected)
    return status.to_dict()


@router.get("/api/deployment/state")
async def get_deployment_state(request: Request) -> dict:
    """Return dashboard deployment state."""
    return {"nodes": [], "expected": None}


@router.post("/api/deployment/update-signal")
async def post_deployment_update_signal(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("system.control")),  # noqa: B008
) -> dict:
    """Emit update event for a node."""
    return {"status": "acknowledged"}


@router.get("/api/deployment/git-drift")
async def get_git_drift() -> dict:
    """Report git drift on deployed dashboards."""
    return {"drift": []}
