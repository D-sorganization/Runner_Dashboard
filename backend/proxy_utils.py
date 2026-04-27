"""Proxy utilities for hub-spoke topology."""

from __future__ import annotations

import logging
import httpx
from fastapi import Request, HTTPException
from dashboard_config import MACHINE_ROLE, HUB_URL

log = logging.getLogger("dashboard.proxy")

async def proxy_to_hub(request: Request):
    """Proxy request to the designated HUB_URL for hub-spoke topology."""
    if not HUB_URL:
        raise HTTPException(status_code=502, detail="HUB_URL not configured")
    async with httpx.AsyncClient(timeout=15.0) as client:
        url = f"{HUB_URL}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        try:
            req = client.build_request(
                request.method,
                url,
                headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")},
                content=await request.body(),
            )
            resp = await client.send(req)
            # Prevent decoding errors on empty/non-json responses if necessary
            if resp.status_code == 204 or not resp.content:
                return {}
            return resp.json()
        except Exception as e:
            log.warning("Hub proxy error for %s: %s", request.url.path, e)
            raise HTTPException(status_code=502, detail="Hub proxy error") from e

def should_proxy_fleet_to_hub(request: Request) -> bool:
    """Return True when this node should use the hub's fleet-wide view."""
    if MACHINE_ROLE != "node" or not HUB_URL:
        return False
    local_value = request.query_params.get("local", "").lower()
    scope_value = request.query_params.get("scope", "").lower()
    return local_value not in {"1", "true", "yes", "local"} and scope_value != "local"
