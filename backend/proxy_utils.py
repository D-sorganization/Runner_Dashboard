"""Proxy utilities for hub-spoke topology."""

from __future__ import annotations

import logging

import httpx
from dashboard_config import HUB_URL, MACHINE_ROLE
from fastapi import HTTPException, Request

log = logging.getLogger("dashboard.proxy")


def _translate_upstream_response(resp: httpx.Response, upstream_name: str, request_id: str = "") -> dict:
    if resp.status_code == 204:
        return {"status": "no_content"}
    if not resp.headers.get("content-type", "").startswith("application/json"):
        body_snippet = resp.content[:200].decode("utf-8", errors="replace") if hasattr(resp, "content") else ""
        log.warning(
            "[%s] %s returned non-JSON (%d). Body: %s", request_id, upstream_name, resp.status_code, body_snippet
        )
        raise HTTPException(status_code=502, detail=f"{upstream_name} returned non-JSON ({resp.status_code})")
    return resp.json()


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
            return _translate_upstream_response(resp, "Hub proxy")
        except httpx.TimeoutException as e:
            log.warning("Hub proxy timeout for %s: %s", request.url.path, e)
            raise HTTPException(status_code=504, detail="Hub timeout") from e
        except httpx.ConnectError as e:
            log.warning("Hub proxy connect error for %s: %s", request.url.path, e)
            raise HTTPException(status_code=503, detail="Hub connection error") from e
        except HTTPException:
            raise
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
