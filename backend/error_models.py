"""Shared error response models for consistent API error envelopes.

All 4xx/5xx responses should use these models to guarantee a uniform shape:

    {
        "error": "<machine-readable code>",
        "detail": "<human-readable description>",
        "request_id": "<optional trace id>"
    }

Usage in a route handler::

    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="runner not found")

The ``error_handler`` registered in ``server.py`` converts ``HTTPException``
``detail`` strings into ``ErrorResponse`` JSON automatically.  For routes that
return ``ErrorResponse`` directly (e.g. non-exception code paths), construct
the model and return it with the appropriate ``JSONResponse``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Standard error envelope returned for every 4xx / 5xx response.

    Attributes:
        error:      Machine-readable error code (e.g. ``"not_found"``,
                    ``"validation_error"``, ``"server_error"``).
        detail:     Human-readable description of what went wrong.
        request_id: Optional trace / correlation identifier injected by
                    ``RequestContextMiddleware``.
    """

    error: str = Field(..., description="Machine-readable error code.")
    detail: str = Field(..., description="Human-readable error description.")
    request_id: str | None = Field(
        default=None,
        description="Optional request trace ID for correlation.",
    )

    model_config = {"extra": "forbid"}


# Convenience factories ------------------------------------------------------- #


def not_found(detail: str, *, request_id: str | None = None) -> ErrorResponse:
    """Return a 404 Not Found error envelope."""
    return ErrorResponse(error="not_found", detail=detail, request_id=request_id)


def validation_error(detail: str, *, request_id: str | None = None) -> ErrorResponse:
    """Return a 422 Unprocessable Entity error envelope."""
    return ErrorResponse(error="validation_error", detail=detail, request_id=request_id)


def server_error(detail: str, *, request_id: str | None = None) -> ErrorResponse:
    """Return a 500 Internal Server Error envelope."""
    return ErrorResponse(error="server_error", detail=detail, request_id=request_id)


def bad_gateway(detail: str, *, request_id: str | None = None) -> ErrorResponse:
    """Return a 502 Bad Gateway error envelope."""
    return ErrorResponse(error="bad_gateway", detail=detail, request_id=request_id)


def rate_limited(detail: str, *, request_id: str | None = None) -> ErrorResponse:
    """Return a 429 Too Many Requests error envelope."""
    return ErrorResponse(error="rate_limited", detail=detail, request_id=request_id)


def forbidden(detail: str, *, request_id: str | None = None) -> ErrorResponse:
    """Return a 403 Forbidden error envelope."""
    return ErrorResponse(error="forbidden", detail=detail, request_id=request_id)


def conflict(detail: str, *, request_id: str | None = None) -> ErrorResponse:
    """Return a 409 Conflict error envelope."""
    return ErrorResponse(error="conflict", detail=detail, request_id=request_id)


def service_error(detail: str, code: str = "service_error", *, request_id: str | None = None) -> ErrorResponse:
    """Return an error envelope for a service-level failure (start/stop/restart)."""
    return ErrorResponse(error=code, detail=detail, request_id=request_id)


def service_stderr_to_status(stderr: str) -> int:
    """Map systemd/service stderr text to a semantically correct HTTP status code.

    - "not loaded" / "Unit not found" / "no such file"  → 404
    - "permission denied" / "access denied" / "operation not permitted" → 403
    - anything else → 500
    """
    lower = stderr.lower()
    if "not loaded" in lower or "unit not found" in lower or "no such file" in lower:
        return 404
    if "permission denied" in lower or "access denied" in lower or "operation not permitted" in lower:
        return 403
    return 500
