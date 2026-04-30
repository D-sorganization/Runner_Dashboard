"""Fleet orchestration and audit log routes."""

import json
import logging
from datetime import datetime, UTC
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_principal  # noqa: B008

log = logging.getLogger("dashboard")
router = APIRouter()

_ORCHESTRATION_AUDIT_PATH = Path.home() / "actions-runners" / "dashboard" / "orchestration_audit.json"


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


@router.get("/api/fleet/orchestration")
async def get_fleet_orchestration(request: Request) -> dict:
    """Return per-machine job assignment, queue, and capacity for fleet orchestration view."""
    audit_entries = _load_orchestration_audit(limit=10)
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "machines": [],
        "online_count": 0,
        "total_count": 0,
        "audit_log": list(reversed(audit_entries)),
    }


@router.get("/api/fleet/audit")
async def get_fleet_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),  # noqa: B008
) -> list[dict]:
    """Return this fleet's orchestration audit log."""
    return _load_orchestration_audit(limit=limit, principal=principal)


@router.get("/api/audit")
async def get_node_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),  # noqa: B008
) -> list[dict]:
    """Return this node's orchestration audit log."""
    return _load_orchestration_audit(limit=limit, principal=principal)
