"""Request proxying utilities extracted from server.py (issue #299).

Provides:
- Hub proxy for spoke-to-hub routing
- Proxy decision logic based on machine role
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
from fastapi import HTTPException
from fastapi.requests import Request

# Note: HUB_URL and MACHINE_ROLE are defined in server.py based on environment.
# These module-level variables should be imported from server.py by the app.

if TYPE_CHECKING:
    pass

log = logging.getLogger("dashboard")

# These are retrieved from server.py at runtime
HUB_URL: str | None = None
MACHINE_ROLE: str = "node"


def _set_hub_config(hub_url: str | None, machine_role: str) -> None:
    """Configure hub and machine role (called from server.py at startup)."""
    global HUB_URL, MACHINE_ROLE  # noqa: PLW0603
    HUB_URL = hub_url
    MACHINE_ROLE = machine_role


async def proxy_to_hub(request: Request) -> dict:
    """Proxy request to the designated HUB_URL for hub-spoke topology.

    Args:
        request: Incoming FastAPI request

    Returns:
        JSON response from the hub

    Raises:
        HTTPException(502): If HUB_URL not configured or proxy fails
    """
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
        except Exception as e:  # noqa: BLE001
            log.warning("Hub proxy error for %s: %s", request.url.path, e)
            raise HTTPException(status_code=502, detail="Hub proxy error") from e


def should_proxy_fleet_to_hub(request: Request) -> bool:
    """Return True when this node should use the hub's fleet-wide view.

    Local health, system metrics, watchdog, and runner schedule endpoints stay
    local. Fleet-wide endpoints can proxy to the hub, while hub fan-out calls
    can add ``?local=1`` to force a node-local action and avoid proxy loops.

    Args:
        request: Incoming FastAPI request

    Returns:
        True if this node should proxy the request to the hub
    """
    if MACHINE_ROLE != "node" or not HUB_URL:
        return False
    local_value = request.query_params.get("local", "").lower()
    scope_value = request.query_params.get("scope", "").lower()
    return local_value not in {"1", "true", "yes", "local"} and scope_value != "local"
