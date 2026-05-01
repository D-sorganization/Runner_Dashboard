"""Proxy utilities for hub-spoke topology."""

from __future__ import annotations

import logging
import os

import httpx
from dashboard_config import HUB_URL, MACHINE_ROLE
from fastapi import HTTPException, Request

log = logging.getLogger("dashboard.proxy")

# Headers that must NEVER be forwarded to the hub (issue #347).
# Forwarding these would allow credential laundering if HUB_URL is
# misconfigured to point at a host controlled by a different tenant.
_SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "x-api-key",
        "x-csrf-token",
    }
)

# Headers removed for technical reasons (hop-by-hop).
_HOP_BY_HOP_HEADERS: frozenset[str] = frozenset({"host", "content-length"})


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


def _safe_forward_headers(request: Request) -> dict[str, str]:
    """Return a header dict safe to forward to the hub.

    Strips all sensitive headers (Authorization, Cookie, X-API-Key,
    X-CSRF-Token) and hop-by-hop headers.  Injects the intra-fleet bearer
    token (HUB_FLEET_TOKEN) for hub authentication instead.
    """
    forwarded: dict[str, str] = {}
    for key, value in request.headers.items():
        lkey = key.lower()
        if lkey in _SENSITIVE_HEADERS or lkey in _HOP_BY_HOP_HEADERS:
            continue
        forwarded[key] = value

    # Inject intra-fleet bearer token (see docs/runbooks/hub-credentials.md).
    hub_token = os.environ.get("HUB_FLEET_TOKEN", "")
    if hub_token:
        forwarded["Authorization"] = f"Bearer {hub_token}"

    return forwarded


async def proxy_to_hub(request: Request):
    """Proxy request to the designated HUB_URL for hub-spoke topology.

    Sensitive caller headers are stripped and replaced with the intra-fleet
    bearer token so that operator credentials cannot be laundered to the hub
    (issue #347).
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
                headers=_safe_forward_headers(request),
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
