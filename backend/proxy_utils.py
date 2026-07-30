"""Proxy utilities for hub-spoke topology."""

from __future__ import annotations

import logging
import os
import time

import httpx
from dashboard_config import HUB_URL, MACHINE_ROLE
from fastapi import HTTPException, Request

log = logging.getLogger("dashboard.proxy")

# ── Hub circuit breaker (issue: blank dashboard when the hub is offline) ──────
# A spoke proxies fleet-wide endpoints to HUB_URL. If the hub is unreachable the
# proxy used to raise 504/503 on EVERY request, so a dead hub blanked the whole
# dashboard. We now open a short-lived circuit breaker on a hub failure: while it
# is open, ``should_proxy_fleet_to_hub`` returns False so the node serves its OWN
# local data instead of hammering (and timing out on) a dead hub. A successful
# proxy closes the breaker immediately.
HUB_CIRCUIT_COOLDOWN_S = 30.0
_hub_unhealthy_until = 0.0  # monotonic-clock deadline; hub is "down" while now < this


def mark_hub_unreachable(cooldown_s: float = HUB_CIRCUIT_COOLDOWN_S) -> None:
    """Open the hub circuit breaker for ``cooldown_s`` seconds (hub is down)."""
    global _hub_unhealthy_until
    _hub_unhealthy_until = time.monotonic() + cooldown_s


def hub_in_cooldown() -> bool:
    """True while the hub circuit breaker is open (hub recently unreachable)."""
    is_open = time.monotonic() < _hub_unhealthy_until
    _record_hub_circuit_state(is_open)
    return is_open


def reset_hub_circuit() -> None:
    """Close the hub circuit breaker (called on a successful proxy / by tests)."""
    global _hub_unhealthy_until
    _hub_unhealthy_until = 0.0
    _record_hub_circuit_state(False)


def _record_hub_circuit_state(is_open: bool) -> None:
    try:
        from prometheus_metrics import set_hub_circuit_open  # noqa: PLC0415

        set_hub_circuit_open(is_open)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        log.debug("Hub circuit metric update skipped: %s", exc)


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
            reset_hub_circuit()  # hub answered → close the breaker
            return _translate_upstream_response(resp, "Hub proxy")
        except httpx.TimeoutException as e:
            log.warning("Hub proxy timeout for %s: %s", request.url.path, e)
            mark_hub_unreachable()  # open breaker → serve local until it recovers
            raise HTTPException(status_code=504, detail="Hub timeout") from e
        except httpx.ConnectError as e:
            log.warning("Hub proxy connect error for %s: %s", request.url.path, e)
            mark_hub_unreachable()  # open breaker → serve local until it recovers
            raise HTTPException(status_code=503, detail="Hub connection error") from e
        except HTTPException:
            raise
        except Exception as e:
            log.warning("Hub proxy error for %s: %s", request.url.path, e)
            raise HTTPException(status_code=502, detail="Hub proxy error") from e


def _request_prefers_local(request: Request) -> bool:
    local_value = request.query_params.get("local", "").lower()
    scope_value = request.query_params.get("scope", "").lower()
    return local_value in {"1", "true", "yes", "local"} or scope_value == "local"


def should_mark_hub_circuit_degraded(request: Request) -> bool:
    """Return True when local data is served only because the hub circuit is open."""
    return MACHINE_ROLE == "node" and bool(HUB_URL) and hub_in_cooldown() and not _request_prefers_local(request)


def should_proxy_fleet_to_hub(request: Request) -> bool:
    """Return True when this node should use the hub's fleet-wide view.

    When the hub circuit breaker is open (hub recently unreachable) we return
    False so the node serves its own local data instead of timing out on a dead
    hub — preventing a blank dashboard fleet-wide.
    """
    if MACHINE_ROLE != "node" or not HUB_URL or hub_in_cooldown():
        return False
    return not _request_prefers_local(request)
