"""Stable error envelope and deprecation headers for Staff API v1 (SC-F3, Issue #1312).

Specifications:
- Error envelope: {error: {code, message, retryable, hint, request_id}} for every 4xx/5xx under /api/v1/staff.
- Deprecation headers for legacy /api/staff/* endpoints:
  - Deprecation: true
  - Sunset: Wed, 24 Dec 2026 00:00:00 GMT
  - Link: </api/v1/staff/...>; rel="successor-version"
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

log = logging.getLogger("dashboard.staff.v1_envelope")

SUNSET_DATE = "Wed, 24 Dec 2026 00:00:00 GMT"

_STATUS_TO_CODE = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
}

_STATUS_TO_HINT = {
    400: "Check your request parameters and body structure.",
    401: "Provide a valid Bearer token in the Authorization header.",
    403: "Caller lacks the required scope for this operation.",
    404: "The requested resource could not be found.",
    405: "Check supported HTTP methods in the OpenAPI documentation.",
    409: "The resource is currently conflicted or being modified concurrently.",
    422: "Request body failed validation against the schema.",
    429: "Rate limit exceeded; back off and retry later.",
    500: "An internal server error occurred; check server logs.",
    502: "Upstream worker or provider gateway failed to respond.",
    503: "Service is temporarily unavailable; operation is retryable.",
    504: "Upstream operation timed out.",
}


class StaffErrorDetail(BaseModel):
    """Inner error payload for the stable staff v1 error envelope."""

    code: str = Field(description="Machine-readable error classification code")
    message: str = Field(description="Human-readable explanation of the error")
    retryable: bool = Field(default=False, description="Whether client may retry this request safely")
    hint: str = Field(default="", description="Guidance or remediation step for the caller")
    request_id: str = Field(default="", description="Unique tracking identifier for the request")


class StaffErrorEnvelope(BaseModel):
    """Top-level response envelope for all error responses on /api/v1/staff."""

    error: StaffErrorDetail


def format_error_envelope(
    status_code: int,
    detail: Any,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Format an arbitrary FastAPI/Starlette error detail into the standard v1 envelope."""
    req_id = request_id or getattr(log, "request_id", None) or f"req-{uuid4().hex[:12]}"
    code = _STATUS_TO_CODE.get(status_code, "error")
    retryable = status_code in (429, 502, 503, 504)
    hint = _STATUS_TO_HINT.get(status_code, "Consult /api/v1/staff documentation.")

    if isinstance(detail, dict):
        code = str(detail.get("code") or code)
        if "required_scope" in detail:
            scope = detail["required_scope"]
            message = f"Authorization failed: missing required scope '{scope}'"
            hint = f"Caller requires the '{scope}' scope."
        elif isinstance(detail.get("error"), str):
            message = detail["error"]
        elif "message" in detail:
            message = str(detail["message"])
        elif "detail" in detail:
            message = str(detail["detail"])
        else:
            message = _STATUS_TO_CODE.get(status_code, "error")
        if "retryable" in detail:
            retryable = bool(detail["retryable"])
        hint = str(detail.get("hint") or hint)
        req_id = str(detail.get("request_id") or req_id)
    elif isinstance(detail, list):
        # Validation error array from FastAPI / Pydantic
        code = "validation_error"
        messages = []
        for err in detail:
            if isinstance(err, dict):
                loc = ".".join(str(x) for x in err.get("loc", []))
                msg = err.get("msg", "invalid")
                messages.append(f"{loc}: {msg}" if loc else msg)
            else:
                messages.append(str(err))
        message = "; ".join(messages) if messages else "Validation failed"
        hint = "Check schema in OpenAPI docs."
    else:
        message = str(detail) if detail else _STATUS_TO_CODE.get(status_code, "error")

    envelope = StaffErrorEnvelope(
        error=StaffErrorDetail(
            code=code,
            message=message,
            retryable=retryable,
            hint=hint,
            request_id=req_id,
        )
    )
    return envelope.model_dump()


class StaffV1Middleware(BaseHTTPMiddleware):
    """Middleware enforcing error envelope for /api/v1/staff and deprecation headers for /api/staff."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Generate or capture request_id
        req_id = request.headers.get("X-Request-Id") or f"req-{uuid4().hex[:12]}"
        request.state.request_id = req_id

        response = await call_next(request)

        # 1. Handle legacy /api/staff/* paths (exclude /api/v1/staff/*)
        if path.startswith("/api/staff") and not path.startswith("/api/v1/staff"):
            subpath = path[len("/api/staff") :]
            v1_target = f"/api/v1/staff{subpath}"
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = SUNSET_DATE
            response.headers["Link"] = f'<{v1_target}>; rel="successor-version"'
            return response

        # 2. Handle /api/v1/staff/* error envelope
        if path.startswith("/api/v1/staff") and response.status_code >= 400:
            content_type = response.headers.get("content-type", "")
            # Buffer the response body to inspect/transform
            if hasattr(response, "body_iterator") and response.body_iterator is not None:  # type: ignore[attr-defined]
                body_chunks = [chunk async for chunk in response.body_iterator]  # type: ignore[attr-defined]
                body_bytes = b"".join(body_chunks)
            elif hasattr(response, "body"):
                body_bytes = bytes(getattr(response, "body", b""))
            else:
                body_bytes = b""

            parsed: Any = None
            if "application/json" in content_type:
                try:
                    parsed = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    parsed = body_bytes.decode("utf-8", errors="replace")
            else:
                parsed = body_bytes.decode("utf-8", errors="replace")

            # If it's already an envelope with {error: {code, message...}}, preserve it
            if isinstance(parsed, dict) and "error" in parsed and isinstance(parsed["error"], dict):
                err_dict = parsed["error"]
                if "code" in err_dict and "message" in err_dict:
                    if not err_dict.get("request_id"):
                        err_dict["request_id"] = req_id
                    return JSONResponse(
                        status_code=response.status_code,
                        content={"error": err_dict},
                        headers=dict(response.headers),
                    )

            detail = parsed.get("detail") if isinstance(parsed, dict) and "detail" in parsed else parsed
            envelope_dict = format_error_envelope(response.status_code, detail, request_id=req_id)

            new_headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            new_headers["X-Request-Id"] = req_id
            return JSONResponse(
                status_code=response.status_code,
                content=envelope_dict,
                headers=new_headers,
            )

        return response
