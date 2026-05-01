# ruff: noqa: B008
"""Fleet orchestration audit routes extracted from routers/orchestration.py."""

from __future__ import annotations

import asyncio
import datetime as _dt_mod
import logging
from typing import Any

import httpx
import orchestration_audit as _audit
from dashboard_config import FLEET_NODES
from fastapi import APIRouter, Depends, Request
from identity import Principal, require_principal

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017

log = logging.getLogger("dashboard.orchestration")
router = APIRouter(tags=["orchestration"])


@router.get("/api/audit", tags=["fleet"])
async def get_node_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),
) -> list[dict]:
    """Return this node's orchestration audit log."""
    return _audit.load_orchestration_audit(limit=limit, principal=principal)


@router.get("/api/fleet/audit", tags=["fleet"])
async def get_fleet_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),
) -> dict:
    """Return a merged view of orchestration audit logs across the fleet."""
    local_entries = _audit.load_orchestration_audit(limit=limit, principal=principal)
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

    def _parse_ts(entry: dict) -> _dt_mod.datetime:
        ts_str = entry.get("timestamp") or entry.get("ts") or ""
        try:
            return _dt_mod.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            return _dt_mod.datetime.min.replace(tzinfo=UTC)

    all_entries.sort(key=_parse_ts, reverse=True)

    return {
        "entries": all_entries[:limit],
        "count": len(all_entries[:limit]),
    }
