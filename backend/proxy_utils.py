"""Proxy utilities for hub-spoke topology."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from dashboard_config import HUB_URL, MACHINE_ROLE
from fastapi import HTTPException, Request

log = logging.getLogger("dashboard.proxy")
MAX_LOG_BODY_CHARS = 500


def _request_id_from_headers(headers: httpx.Headers | dict[str, str] | None) -> str:
    if not headers:
        return "-"
    return headers.get("x-request-id") or headers.get("x-correlation-id") or "-"


def _response_body_preview(resp: httpx.Response) -> str:
    try:
        body = resp.text
    except UnicodeDecodeError:
        body = resp.content.decode("utf-8", errors="replace")
    return body[:MAX_LOG_BODY_CHARS]


def translate_upstream_response(
    resp: httpx.Response,
    *,
    upstream: str,
    request_id: str,
    target_url: str,
) -> dict[str, Any]:
    """Translate an upstream HTTP response into a dashboard payload or error."""
    if resp.status_code == 204:
        return {"status": "no_content"}

    content_type = resp.headers.get("content-type", "").lower()
    is_json = content_type.split(";", 1)[0].strip() == "application/json"
    if not is_json:
        log.warning(
            "Upstream returned non-JSON: upstream=%s request_id=%s url=%s status=%s body=%r",
            upstream,
            request_id,
            target_url,
            resp.status_code,
            _response_body_preview(resp),
        )
        raise HTTPException(
            status_code=resp.status_code if resp.status_code >= 400 else 502,
            detail={"upstream": upstream, "status": resp.status_code, "detail": "upstream returned non-JSON"},
        )

    payload = resp.json()
    if resp.status_code >= 400:
        log.warning(
            "Upstream returned error: upstream=%s request_id=%s url=%s status=%s body=%r",
            upstream,
            request_id,
            target_url,
            resp.status_code,
            _response_body_preview(resp),
        )
        raise HTTPException(
            status_code=resp.status_code,
            detail={"upstream": upstream, "status": resp.status_code, "detail": payload},
        )
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail={"upstream": upstream, "status": resp.status_code, "detail": "upstream returned non-object JSON"},
        )
    return payload


async def proxy_to_hub(request: Request):
    """Proxy request to the designated HUB_URL for hub-spoke topology."""
    if not HUB_URL:
        raise HTTPException(status_code=502, detail="HUB_URL not configured")
    async with httpx.AsyncClient(timeout=15.0) as client:
        url = f"{HUB_URL}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        request_id = _request_id_from_headers(dict(request.headers))
        try:
            req = client.build_request(
                request.method,
                url,
                headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")},
                content=await request.body(),
            )
            resp = await client.send(req)
            return translate_upstream_response(resp, upstream="hub", request_id=request_id, target_url=url)
        except httpx.TimeoutException as e:
            log.warning("Hub proxy timeout: request_id=%s url=%s", request_id, url)
            raise HTTPException(status_code=504, detail="Hub proxy timeout") from e
        except httpx.ConnectError as e:
            log.warning("Hub proxy connect error: request_id=%s url=%s error=%s", request_id, url, e)
            raise HTTPException(status_code=503, detail="Hub unavailable") from e


def should_proxy_fleet_to_hub(request: Request) -> bool:
    """Return True when this node should use the hub's fleet-wide view."""
    if MACHINE_ROLE != "node" or not HUB_URL:
        return False
    local_value = request.query_params.get("local", "").lower()
    scope_value = request.query_params.get("scope", "").lower()
    return local_value not in {"1", "true", "yes", "local"} and scope_value != "local"
