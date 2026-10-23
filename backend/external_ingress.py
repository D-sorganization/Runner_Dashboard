"""Machine-credential boundary for requests arriving from external ingress."""

from __future__ import annotations

import logging
import os
import uuid
from urllib.parse import urlsplit

from browser_origin import external_browser_request
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from request_context import request_id_ctx


def _is_external_ingress(request: Request) -> bool:
    """Recognize edge-marked ingress without trusting it to grant identity."""
    if external_browser_request(request):
        return True
    origin = request.headers.get("origin")
    if not origin:
        return False
    try:
        host = (urlsplit(origin).hostname or "").casefold()
    except ValueError:
        return True
    return host not in {"localhost", "127.0.0.1", "::1"}


def _log_denial(
    request: Request,
    logger: logging.Logger,
    reason: str,
    detail: str = "",
    *,
    message: str = "external ingress request denied",
) -> str:
    client_host = request.client.host if request.client else "unknown"
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    request_id_token = request_id_ctx.set(request_id)
    try:
        logger.warning(
            message,
            extra={
                "request_id": request_id,
                "source_ip": client_host,
                "cf_ray": request.headers.get("cf-ray"),
                "path": request.url.path,
                "method": request.method,
                "reason": reason,
                "denial_detail": detail,
            },
        )
    finally:
        request_id_ctx.reset(request_id_token)
    return request_id


def deny_external_ingress_bearer(request: Request, logger: logging.Logger) -> JSONResponse | None:
    """Return a generic denial for machine bearer tokens at external ingress."""
    authorization = request.headers.get("authorization", "")
    if not _is_external_ingress(request) or authorization.partition(" ")[0].casefold() != "bearer":
        return None
    request_id = _log_denial(
        request,
        logger,
        "machine_credentials_not_allowed_on_external_ingress",
        message="blocked bearer credential on external ingress",
    )
    return JSONResponse({"error": "Request not permitted"}, status_code=403, headers={"X-Request-ID": request_id})


def validated_browser_owner_id(request: Request, github_id: object, principal_type: str | None) -> str | None:
    """Require the configured immutable owner ID and human principal for external OAuth.

    Local callbacks retain their existing principal policy. No header grants an
    identity: edge markers only select the stricter validation path.
    """
    user_id = str(github_id) if type(github_id) is int and github_id > 0 else None
    if not _is_external_ingress(request):
        return user_id
    configured = os.environ.get("DASHBOARD_OWNER_GITHUB_ID", "").strip()
    configured_valid = configured.isascii() and configured.isdecimal() and not configured.startswith("0")
    if configured_valid and user_id == configured and principal_type == "human":
        return user_id
    detail = "owner_configuration_missing_or_invalid" if not configured_valid else "owner_or_human_identity_mismatch"
    request_id = _log_denial(
        request, logging.getLogger("dashboard.external_ingress"), "external_oauth_owner_denied", detail
    )
    request.session.clear()
    raise HTTPException(status_code=403, detail="Request not permitted", headers={"X-Request-ID": request_id})
