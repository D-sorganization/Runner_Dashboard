"""Diagnostics logs endpoint for fleet agents (#2235).

Read-only endpoint GET /api/v1/diagnostics/logs with strict caps (<=500 records, <=256 KB).
"""

from __future__ import annotations

import logging
from typing import Any

from diagnostics_logs import MAX_QUERY_LIMIT, get_log_ring_buffer
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_scope

log = logging.getLogger("dashboard.routers.diagnostics_logs")
router = APIRouter(tags=["diagnostics"])
_STAFF_READ = require_scope("staff.read")


@router.get("/logs")
async def get_diagnostics_logs(
    request: Request,
    _peer: Principal = Depends(_STAFF_READ),  # noqa: B008
) -> dict[str, Any]:
    """Retrieve ring-buffer log entries with redacting and response caps."""
    params = request.query_params
    try:
        limit = int(params.get("limit", 100))
    except ValueError:
        raise HTTPException(status_code=422, detail="limit must be an integer") from None

    if limit > MAX_QUERY_LIMIT:
        raise HTTPException(status_code=422, detail=f"limit must be <= {MAX_QUERY_LIMIT} (received {limit})")

    node = params.get("node")
    level = params.get("level")
    logger = params.get("logger")
    run_id = params.get("run_id")
    request_id = params.get("request_id")
    since = params.get("since")

    if node:
        from staff.runner import get_runner

        if node != get_runner().machine:
            from staff import fleet as staff_fleet

            nodes = staff_fleet.list_peers() if hasattr(staff_fleet, "list_peers") else []
            peer_url = next((p.get("url") for p in nodes if p.get("name") == node), None)
            if peer_url:
                filters = [
                    ("level", level),
                    ("logger", logger),
                    ("run_id", run_id),
                    ("request_id", request_id),
                    ("since", since),
                ]
                qp = [f"limit={limit}"] + [f"{k}={v}" for k, v in filters if v]
                target_url = f"{peer_url}/api/v1/diagnostics/logs?{'&'.join(qp)}"
                try:
                    return await staff_fleet.get_json(target_url, staff_fleet.fleet_headers())
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to proxy diagnostics logs to node %s: %s", node, exc)
                    return {"logs": [], "count": 0, "truncated": False, "limit": limit, "node_error": str(exc)}

    buf = get_log_ring_buffer()
    records, truncated = buf.query(
        level=level, logger=logger, run_id=run_id, request_id=request_id, since=since, limit=limit
    )
    return {"logs": records, "count": len(records), "truncated": truncated, "limit": limit}
