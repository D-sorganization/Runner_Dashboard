"""Machine-credential boundary for requests arriving from external ingress."""

from __future__ import annotations

import logging
import uuid
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from request_context import request_id_ctx


def _is_external_ingress(request: Request) -> bool:
    """Recognize edge-marked ingress without trusting it to grant identity."""
    if any(
        request.headers.get(header)
        for header in ("cf-ray", "cf-connecting-ip", "cf-access-jwt-assertion", "cf-visitor")
    ):
        return True
    origin = request.headers.get("origin")
    if not origin:
        return False
    try:
        host = (urlsplit(origin).hostname or "").casefold()
    except ValueError:
        return True
    return host not in {"localhost", "127.0.0.1", "::1"}


def deny_external_ingress_bearer(request: Request, logger: logging.Logger) -> JSONResponse | None:
    """Return a generic denial for machine bearer tokens at external ingress."""
    authorization = request.headers.get("authorization", "")
    if not _is_external_ingress(request) or authorization.partition(" ")[0].casefold() != "bearer":
        return None

    client_host = request.client.host if request.client else "unknown"
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    request_id_token = request_id_ctx.set(request_id)
    try:
        logger.warning(
            "blocked bearer credential on external ingress",
            extra={
                "request_id": request_id,
                "source_ip": client_host,
                "cf_ray": request.headers.get("cf-ray"),
                "path": request.url.path,
                "method": request.method,
                "reason": "machine_credentials_not_allowed_on_external_ingress",
            },
        )
    finally:
        request_id_ctx.reset(request_id_token)
    return JSONResponse({"error": "Request not permitted"}, status_code=403, headers={"X-Request-ID": request_id})
