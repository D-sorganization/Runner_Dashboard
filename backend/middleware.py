"""Middleware extracted from server.py."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

_AUTH_EXEMPT_PATHS = {
    "/",
    "/health",
    "/api/health",
    "/manifest.webmanifest",
    "/icon.svg",
    "/api/auth/github",
    "/api/auth/callback",
    "/api/linear/webhook",
}


async def csrf_check(request: Request, call_next: Any) -> Any:
    """Reject state-changing requests that lack the CSRF sentinel header (issue #30)."""
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        # Skip exempt paths (e.g. external webhooks)
        if request.url.path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)
        # Allow health / static routes without the header so monitoring tools
        # (e.g. curl health checks) still work.  Only enforce on /api/* paths.
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/linear/webhook"):
            if request.headers.get("X-Requested-With") != "XMLHttpRequest":
                return JSONResponse(
                    {"error": "CSRF check failed: missing X-Requested-With header"},
                    status_code=403,
                )
    return await call_next(request)


async def add_security_headers(request: Request, call_next: Any) -> Any:
    """Inject standard security headers on every response (issue #7, #18)."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'strict-dynamic' "
        "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self' data:;"
    )
    return response
