"""Bound browser CSRF proofs for the external human conversation plane."""

from __future__ import annotations

import logging
import re
import uuid

import dashboard_config
from browser_origin import browser_origin, external_browser_request
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from identity import Principal
from itsdangerous import BadSignature, URLSafeTimedSerializer
from request_context import request_id_ctx

_SCOPE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_THREAD = re.compile(r"/api/v1/staff/(?:threads|panels)/([A-Za-z0-9_-]{1,128})(?:/.*)?\Z")
_MUTATIONS = {"POST", "PUT", "PATCH", "DELETE"}
_TTL_SECONDS = 15 * 60


def _audit_denial(request: Request, reason: str) -> str:
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex[:12]
    token = request_id_ctx.set(request_id)
    try:
        logging.getLogger(__name__).warning(
            "browser CSRF request denied",
            extra={
                "request_id": request_id,
                "source_ip": request.client.host if request.client else "unknown",
                "cf_ray": request.headers.get("cf-ray"),
                "path": request.url.path,
                "method": request.method,
                "reason": reason,
            },
        )
    finally:
        request_id_ctx.reset(token)
    return request_id


def _origin_valid(request: Request) -> bool:
    expected = browser_origin()
    return bool(
        expected
        and request.headers.get("origin") == expected
        and request.headers.get("x-requested-with") == "XMLHttpRequest"
    )


def _binding(request: Request, principal: Principal, scope: str) -> list[str]:
    session_id = request.session.get("session_id")
    if (
        principal.type != "human"
        or not isinstance(session_id, str)
        or not session_id
        or request.session.get("principal_id") != principal.id
        or not _SCOPE.fullmatch(scope)
    ):
        raise ValueError("invalid browser session binding")
    return [session_id, principal.id, scope]


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(dashboard_config.SESSION_SECRET, salt="runner-dashboard:browser-csrf:v1")


def issue_browser_csrf(request: Request, principal: Principal, thread_id: str) -> str:
    """Issue a short proof only to a same-origin authenticated human session."""
    try:
        if not _origin_valid(request):
            raise ValueError("invalid browser origin")
        return _signer().dumps(_binding(request, principal, thread_id))
    except ValueError:
        _audit_denial(request, "browser_csrf_issuance_denied")
        raise HTTPException(status_code=403, detail="Request not permitted") from None


def browser_csrf_denial(request: Request, principal: Principal) -> JSONResponse | None:
    """Reject external conversation mutations before their handler writes data."""
    if request.method not in _MUTATIONS or not external_browser_request(request):
        return None
    path = request.url.path
    match = _THREAD.fullmatch(path)
    scope = "_new" if path in {"/api/v1/staff/threads", "/api/v1/staff/panels"} else match[1] if match else None
    if scope is None:
        return None
    try:
        token = request.headers.get("x-csrf-token", "")
        if not _origin_valid(request) or not token or len(token) > 4096:
            raise ValueError("invalid browser proof")
        if _signer().loads(token, max_age=_TTL_SECONDS) != _binding(request, principal, scope):
            raise ValueError("invalid browser binding")
    except (BadSignature, ValueError, TypeError):
        request_id = _audit_denial(request, "browser_csrf_denied")
        return JSONResponse(
            {"error": "Request not permitted"},
            status_code=403,
            headers={"X-CSRF-Required": "1", "X-Request-ID": request_id},
        )
    return None
