"""Middleware extracted from server.py."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("dashboard.middleware")

# --------------------------------------------------------------------------- #
# MaxBodySizeMiddleware  (issue #350)
# --------------------------------------------------------------------------- #
# Default limits per path (bytes).  Overridden per-route via decorator.
_DEFAULT_MAX_BODY = 1 * 1024 * 1024  # 1 MB
_WEBHOOK_MAX_BODY = 256 * 1024  # 256 KB
_STREAMING_MAX_BODY = 10 * 1024 * 1024  # 10 MB

_LIMIT_OVERRIDES: dict[str, int] = {
    "/api/linear/webhook": _WEBHOOK_MAX_BODY,
}

_MAX_BODY_HEADER = "X-Max-Body-Size"


class MaxBodySizeMiddleware:
    """Reject requests whose Content-Length exceeds a per-route cap.

    Falls back to ``X-Max-Body-Size`` header (set by route handlers that
    stream large payloads) and finally to the global default.
    """

    def __init__(self, app: Any, default_limit: int = _DEFAULT_MAX_BODY) -> None:
        self.app = app
        self.default_limit = default_limit

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        limit = _LIMIT_OVERRIDES.get(path, self.default_limit)

        # Allow route handlers to override via header in their own middleware
        # or by setting state before we run.  We inspect headers last so the
        # explicit header wins.
        for name, value in scope.get("headers", []):
            if name.lower() == b"x-max-body-size":
                try:
                    limit = int(value.decode())
                except (ValueError, UnicodeDecodeError):
                    pass
                break

        content_length = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    content_length = int(value.decode())
                except (ValueError, UnicodeDecodeError):
                    pass
                break

        if content_length is not None and content_length > limit:
            # Early reject — don't even start the app
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", b"0"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        await self.app(scope, receive, send)


def max_body_size(limit_bytes: int) -> Callable[[Any], Any]:
    """Decorator for route handlers that need a larger body limit.

    Usage::
        @app.post("/api/upload")
        @max_body_size(10 * 1024 * 1024)
        async def upload(...):
            ...
    """

    def decorator(func: Any) -> Any:
        # Store the limit on the function so the middleware can read it
        # via a custom header injected by a FastAPI dependency or middleware.
        # For simplicity we just note it in a well-known attribute.
        func._max_body_size = limit_bytes  # noqa: B010
        return func

    return decorator


_AUTH_EXEMPT_PATHS = {
    "/",
    "/health",
    "/api/health",
    "/manifest.webmanifest",
    "/icon.svg",
    "/api/auth/github",
    "/api/auth/callback",
    # Logout must succeed even when the presented session is already invalid;
    # it only clears server-side session state and never returns sensitive data.
    "/api/auth/logout",
    "/api/linear/webhook",
    # Webhook receiver health probe — config status only, no sensitive data;
    # consumed by external uptime monitors that present no operator credential.
    "/api/linear/webhook/health",
}

# Routes that authenticate by a mechanism OTHER than a resolved operator
# principal, and therefore must NOT be force-401'd by the structural perimeter
# (#924). Each entry carries an equally-strong, independently-tested check:
#   - /api/fleet/dispatch/*  → HMAC-signed command envelope
#                              (dispatch_contract.validate_envelope_crypto).
#   - /api/orchestrator/*    → Conductor admission gate; feature-flagged off by
#                              default and validated by its own pydantic
#                              contracts + intra-fleet HTTP boundary (#1282).
# These are prefixes; the structural perimeter test cross-checks that anything
# listed here is genuinely a known alternate-auth surface rather than a hole.
#   - /api/credentials/*     → loopback-only guard with proxy-header rejection
#                              (credentials._require_local_request). These write
#                              provider keys to the operator's OWN machine and
#                              must work from the local browser before any login,
#                              so a principal cannot be required; the local-origin
#                              check is the (independently tested) alternate auth.
_ALT_AUTH_EXEMPT_PREFIXES = (
    "/api/fleet/dispatch/",
    "/api/orchestrator/",
    "/api/credentials/",
)

DEFAULT_MAX_BODY_SIZE = 1 * 1024 * 1024  # 1 MB
WEBHOOK_MAX_BODY_SIZE = 256 * 1024  # 256 KB


def limit_body_size(max_bytes: int) -> Callable[[Callable], Callable]:
    """Decorator to set a per-route maximum body size in bytes.

    Usage::

        @router.post("/webhook")
        @limit_body_size(256 * 1024)
        async def my_handler(request: Request): ...

    The middleware inspects the matched route's endpoint for this marker.
    """

    def decorator(func: Callable) -> Callable:
        func.__max_body_size__ = max_bytes  # type: ignore[attr-defined]
        return func

    return decorator


def _get_route_body_limit(request: Request) -> int | None:
    """Return the body size limit for the matched route, or None for default."""
    route = request.scope.get("route")
    if route is None:
        return None

    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return None

    # FastAPI may wrap endpoints; walk the __wrapped__ chain.
    candidate: Any = endpoint
    while candidate is not None:
        limit = getattr(candidate, "__max_body_size__", None)
        if isinstance(limit, int):
            return limit
        candidate = getattr(candidate, "__wrapped__", None)

    return None


async def max_body_size_check(request: Request, call_next: Any) -> Any:
    """Reject requests whose Content-Length exceeds the route limit.

    Default limit is 1 MB.  Routes may override via ``@limit_body_size(bytes)``.
    Requests without a Content-Length header (streaming / chunked) are allowed.
    """
    # Only enforce for mutating methods that typically carry a body.
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return await call_next(request)

    content_length = request.headers.get("content-length")
    if content_length is None:
        # No Content-Length header — allow through (streaming/chunked).
        return await call_next(request)

    try:
        body_len = int(content_length)
    except ValueError:
        return JSONResponse(
            {"error": "Invalid Content-Length header"},
            status_code=400,
        )

    route_limit = _get_route_body_limit(request)
    if route_limit is None:
        limit = DEFAULT_MAX_BODY_SIZE
    else:
        limit = route_limit

    if body_len > limit:
        log.warning(
            "max_body_size_check: rejecting %s %s — body %d bytes > limit %d bytes",
            request.method,
            request.url.path,
            body_len,
            limit,
        )
        return JSONResponse(
            {"error": f"Request body too large ({body_len} bytes > {limit} bytes)"},
            status_code=413,
        )

    return await call_next(request)


def is_auth_exempt(path: str) -> bool:
    """Return True if *path* is exempt from the structural auth perimeter (#924).

    Exemptions cover health probes, the auth handshake endpoints (which mint the
    very credentials the perimeter requires), the inbound Linear webhook (its own
    signature check authenticates it), the PWA manifest, and the SPA shell / app
    icon served as static assets. Static asset paths (anything not under
    ``/api/``) are handled separately by the caller and are not listed here.

    Alternate-auth surfaces (HMAC dispatch envelopes, the feature-flagged
    Conductor orchestrator) are also treated as exempt from the *principal*
    perimeter because they enforce their own equally-strong check.
    """
    if path in _AUTH_EXEMPT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _ALT_AUTH_EXEMPT_PREFIXES)


def _requires_perimeter_auth(request: Request) -> bool:
    """Return True when this request must carry an authenticated principal (#924).

    Only ``/api/*`` routes are gated; the SPA shell, static assets, and the
    explicitly exempt handshake/health/webhook paths pass through. This is the
    single structural decision point so every present and future ``/api/*`` route
    is authenticated by default rather than opt-in per route.
    """
    path = request.url.path
    if not path.startswith("/api/"):
        return False
    return not is_auth_exempt(path)


async def auth_perimeter_check(request: Request, call_next: Any) -> Any:
    """Fail-closed structural auth gate for every ``/api/*`` route (#924).

    Authentication used to be opt-in per route: a handler that simply forgot to
    declare ``Depends(require_principal)`` shipped wide open. This middleware
    enforces a perimeter so a missing per-route dependency can no longer create
    an unauthenticated hole — every non-exempt ``/api/*`` request must resolve to
    a principal (service token, session, or gated loopback admin) or it is
    rejected with 401 before the handler runs.

    The resolved principal is stashed on ``request.state.perimeter_principal`` so
    downstream dependencies can reuse it without re-resolving.

    Test harnesses inject identities via ``app.dependency_overrides`` rather than
    real credentials; when such an override for the auth dependency is present we
    defer to the route-level dependency (which the override satisfies) instead of
    re-checking here, so the perimeter never contradicts an explicit test
    injection. Production has no overrides, so the perimeter is always live.
    """
    if not _requires_perimeter_auth(request):
        return await call_next(request)

    # Defer to the route dependency when a test/explicit override is installed.
    app = request.scope.get("app")
    overrides = getattr(app, "dependency_overrides", {}) if app is not None else {}
    if overrides:
        from identity import require_fleet_peer, require_principal

        if require_principal in overrides or require_fleet_peer in overrides:
            return await call_next(request)

    from identity import resolve_perimeter_principal

    principal = resolve_perimeter_principal(request)
    if principal is None:
        return JSONResponse(
            {"error": "Authentication required"},
            status_code=401,
        )

    request.state.perimeter_principal = principal
    return await call_next(request)


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
    # CSP: 'strict-dynamic' requires per-script nonces and silently makes
    # 'self' a no-op in CSP3 browsers — it blocks the Vite module bundle
    # entirely (blank white screen). Until server-side nonce injection is
    # implemented (issue #324), use plain 'self' for scripts.
    # Google Fonts (googleapis/gstatic) are explicitly allowed since
    # index.html loads them at runtime.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none';"
    )
    # HSTS: instruct browsers to use HTTPS for 1 year; include subdomains
    # (issue #324). Only sent in TLS mode (issue #930): sending HSTS on the
    # documented plain-HTTP-over-tailnet deployment would wedge browsers into
    # HTTPS-only for a year against a server that does not speak TLS.
    from dashboard_config import TLS_ENABLED  # noqa: PLC0415

    if TLS_ENABLED:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Permissions-Policy: microphone allowed on self for VoiceInputButton.
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(self), geolocation=(), payment=(), usb=(), interest-cohort=()"
    )
    return response


# --------------------------------------------------------------------------- #
# IP-keyed rate limiter (issue #320)                                          #
# --------------------------------------------------------------------------- #
# Strict limits for unauthenticated auth endpoints.
_AUTH_RATE_LIMIT = 5  # max attempts per window
_AUTH_RATE_WINDOW = 300  # seconds (5 minutes)

# Store: { ip: [timestamp, ...] }
_auth_rate_store: dict[str, list[float]] = defaultdict(list)


def check_auth_rate_limit(request: Any) -> None:
    """Raise HTTP 429 when the calling IP exceeds 5 attempts per 5 minutes.

    Intended for unauthenticated endpoints like /api/auth/dev-login and
    /api/auth/callback where brute-force / spam attacks are a real risk
    (issue #320).  Key is the client IP address.
    """
    from fastapi import HTTPException  # local import to avoid circular deps

    client = getattr(request, "client", None)
    ip = client.host if client else "unknown"

    now = time.monotonic()
    window = [t for t in _auth_rate_store[ip] if now - t < _AUTH_RATE_WINDOW]
    if len(window) >= _AUTH_RATE_LIMIT:
        log.warning("auth_rate_limit: IP %s exceeded %d attempts in %ds", ip, _AUTH_RATE_LIMIT, _AUTH_RATE_WINDOW)
        raise HTTPException(
            status_code=429,
            detail="Too many authentication attempts. Please wait 5 minutes before retrying.",
            headers={"Retry-After": str(_AUTH_RATE_WINDOW)},
        )
    window.append(now)
    _auth_rate_store[ip] = window
