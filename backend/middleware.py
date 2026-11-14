"""Middleware extracted from server.py."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from external_ingress import deny_external_ingress_bearer
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.routing import Match

log = logging.getLogger("dashboard.middleware")

# --------------------------------------------------------------------------- #
# MaxBodySizeMiddleware  (issue #350; hardened for #4274 WP19 F-07)
# --------------------------------------------------------------------------- #
# Contract: no handler ever receives more request-body bytes than its limit.
# The limit is DEFAULT_MAX_BODY_SIZE unless the matched route's endpoint is
# marked with ``@limit_body_size``; nothing a client sends can change it.
DEFAULT_MAX_BODY_SIZE = 1 * 1024 * 1024  # 1 MB
WEBHOOK_MAX_BODY_SIZE = 256 * 1024  # 256 KB
_BODY_LIMIT_ATTR = "__max_body_size__"


def _require_positive_limit(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer number of bytes, got {value!r}")
    return value


def limit_body_size(max_bytes: int) -> Callable[[Callable], Callable]:
    """Decorator to set a per-route maximum body size in bytes.

    Usage::

        @router.post("/webhook")
        @limit_body_size(256 * 1024)
        async def my_handler(request: Request): ...

    ``MaxBodySizeMiddleware`` resolves this marker for the matched route, so a
    route may be tighter or (explicitly) larger than the default.
    """
    _require_positive_limit(max_bytes, "max_bytes")

    def decorator(func: Callable) -> Callable:
        setattr(func, _BODY_LIMIT_ATTR, max_bytes)
        return func

    return decorator


def endpoint_body_limit(endpoint: Any) -> int | None:
    """Return the ``@limit_body_size`` marker of *endpoint*, or None for the default."""
    # FastAPI may wrap endpoints; walk the __wrapped__ chain.
    candidate: Any = endpoint
    while candidate is not None:
        limit = getattr(candidate, _BODY_LIMIT_ATTR, None)
        if isinstance(limit, int):
            return limit
        candidate = getattr(candidate, "__wrapped__", None)
    return None


def _declared_content_length(scope: Any) -> int | None:
    """Return the declared Content-Length, or None; raise ValueError if malformed."""
    declared: int | None = None
    for name, value in scope.get("headers", ()):
        if name.lower() != b"content-length":
            continue
        text = value.decode("latin-1").strip()
        if not (text.isascii() and text.isdigit()):
            raise ValueError("malformed Content-Length")
        declared = max(declared or 0, int(text))
    return declared


async def _send_json_error(send: Any, status: int, message: str) -> None:
    body = json.dumps({"error": message}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class _BodyTooLargeError(HTTPException):
    """Raised from ``receive`` once the body crosses the limit (an HTTPException so
    FastAPI's body parsing re-raises it instead of masking it as a 400)."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Request body too large")


class MaxBodySizeMiddleware:
    """The single enforcement point for request body size (pure ASGI).

    * A declared ``Content-Length`` above the route's limit is rejected with 413
      before the application runs; a malformed one with 400.
    * The bytes actually received are counted as they stream in, whatever the
      method and whether or not a Content-Length was sent (chunked) or was
      understated. The request is answered 413 as soon as the count crosses the
      limit, so an oversized body is never buffered or handed to a handler.
    * A route whose endpoint is not visible before routing (nested router or
      mounted application) is resolved from ``scope["route"]`` when its body is
      first read, and falls back to the default limit.
    """

    def __init__(self, app: Any, default_limit: int = DEFAULT_MAX_BODY_SIZE) -> None:
        self.app = app
        self.default_limit = _require_positive_limit(default_limit, "default_limit")

    def _route_limit(self, scope: Any) -> int | None:
        """Limit of the route serving this request; None while it cannot be known yet."""
        route = scope.get("route")  # set by the router once the request is routed
        if route is None:
            # Not routed yet: apply the router's own first-full-match rule.
            for candidate in getattr(scope.get("app"), "routes", None) or ():
                match, child_scope = candidate.matches(scope)
                if match is Match.FULL:
                    route = child_scope.get("route", candidate)
                    break
            else:
                return self.default_limit
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            return None
        limit = endpoint_body_limit(endpoint)
        return self.default_limit if limit is None else limit

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        where = f"{scope.get('method')} {scope.get('path')}"
        try:
            declared = _declared_content_length(scope) or 0
        except ValueError:
            await _send_json_error(send, 400, "Invalid Content-Length header")
            return
        # Bodyless requests skip route resolution; None means "resolve on first byte".
        limit = self._route_limit(scope) if declared else None
        if limit is not None and declared > limit:
            log.warning("max_body_size: rejecting %s: declared %d bytes > limit %d bytes", where, declared, limit)
            await _send_json_error(send, 413, f"Request body too large ({declared} bytes > {limit} bytes)")
            return

        received = 0
        exceeded = False
        started = False

        async def counted_receive() -> Any:
            nonlocal limit, received, exceeded
            if exceeded:
                raise _BodyTooLargeError()
            message = await receive()
            if message["type"] == "http.request" and message.get("body"):
                if limit is None:
                    limit = self._route_limit(scope) or self.default_limit
                received += len(message["body"])
                if max(received, declared) > limit:
                    exceeded = True
                    log.warning("max_body_size: rejecting %s: body exceeds limit %d bytes", where, limit)
                    raise _BodyTooLargeError()
            return message

        async def guarded_send(message: Any) -> None:
            nonlocal started
            if exceeded and not started:
                return  # the 413 below replaces whatever the application answers
            started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except Exception:
            if not exceeded or started:
                raise
        if exceeded and not started:
            await _send_json_error(send, 413, f"Request body too large (more than {limit} bytes)")


_AUTH_EXEMPT_PATHS = {
    "/",
    "/health",
    "/api/health",
    # Read-only fleet telemetry consumed hub→node over the Tailscale mesh. The
    # hub's fleet fan-out (`routers.fleet.fetch_node` → GET `/api/system`, and
    # peer-pool GET `/api/fleet/status`) presents no operator principal, and the
    # fleet model already treats these as tailnet-public reads (see
    # `require_fleet_peer` (#922): "fleet reads remain tailnet-public" when no
    # HUB_FLEET_TOKEN is set). The #924 structural perimeter over-blocked them,
    # 401-ing every >=4.9 node so the hub marked the whole fleet offline. These
    # are GET-only system metrics on a private tailnet; `/api/fleet/status` also
    # keeps its own `require_fleet_peer` route dependency.
    "/api/system",
    "/api/fleet/status",
    # GET-only org runner inventory, same telemetry class as `/api/system` /
    # `/api/fleet/status` above. The fleet-health monitor on the hub polls each
    # node's `GET /api/runners` over the tailnet (no operator principal) to read
    # online/busy counts and drive keepalive auto-recovery; the #924 perimeter
    # 401-ed it, so `ct_runners_online` went null and self-healing silently died.
    # Exact-match only — mutating `/api/runners/{id}/start|stop|restart` and the
    # `/api/runners/...` diagnostics POSTs are distinct paths and stay perimeter-
    # protected (they carry their own `require_scope` deps besides).
    "/api/runners",
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
    # GitHub webhook for RD-1 CI-fix dispatch (#1881): authenticated by its own
    # X-Hub-Signature-256 check against GITHUB_WEBHOOK_SECRET, fail closed when unset.
    "/api/remediation/ci-fix/webhook",
    # Client error beacon (issue #1292): reports React/tab crashes so they appear in
    # the event log without operator intervention. Rate-limited by the handler.
    "/api/client-errors",
    # Local anonymous page-view counter (issue #1302 / SC-G1)
    "/api/usage/page-view",
    # Session telemetry ingestion across sessions and fleet hooks (issue #1849 / RD-4)
    "/api/usage/session-telemetry",
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
#   - /api/staff/*           → Staff Hub (epic #1192). Reads use require_fleet_peer
#                              and mutations require_orchestrator_peer, the same
#                              principal / HUB_FLEET_TOKEN / loopback contract as
#                              /api/orchestrator/* so Barb, Orchestrator and the
#                              hub can call it with the fleet bearer token
#                              (tests/api/test_staff_auth_perimeter.py).
#   - /api/priorities*       → Fleet Coordination API priorities (#1227). Reads use
#                              require_fleet_peer so agents holding only the fleet
#                              bearer token can read them; the PUT uses
#                              coordination.auth.require_priorities_writer
#                              (priorities.write scope or loopback). The summary route is the
#                              exact path /api/priorities (in _ALT_AUTH_EXEMPT_EXACT) and the rest the
#                              /api/priorities/ prefix, so /api/prioritiesX is NOT exempt (#1243).
_ALT_AUTH_EXEMPT_EXACT = frozenset({"/api/priorities", "/api/proposals"})
_ALT_AUTH_EXEMPT_PREFIXES = (
    "/api/fleet/dispatch/",
    "/api/orchestrator/",
    "/api/credentials/",
    "/api/staff/",
    "/api/coordination/",
    "/api/v1/agent/",
    "/api/priorities/",
    "/api/proposals/",
)

# Process-local drain switches live outside /api/* but are state-changing (#4274 F-04).
_PRIVILEGED_STATE_PATHS = frozenset({"/_drain", "/_undrain"})


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
    if path in _AUTH_EXEMPT_PATHS or path in _ALT_AUTH_EXEMPT_EXACT:
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

    ingress_denial = deny_external_ingress_bearer(request, log)
    if ingress_denial is not None:
        return ingress_denial

    # Defer to the route dependency when a test/explicit override is installed.
    app = request.scope.get("app")
    overrides = getattr(app, "dependency_overrides", {}) if app is not None else {}
    if overrides:
        from identity import (
            require_fleet_peer,
            require_orchestrator_peer,
            require_principal,
        )

        if require_principal in overrides or require_fleet_peer in overrides or require_orchestrator_peer in overrides:
            return await call_next(request)

    from identity import resolve_perimeter_principal

    principal = resolve_perimeter_principal(request)
    if principal is None:
        return JSONResponse(
            {"error": "Authentication required"},
            status_code=401,
        )

    request.state.perimeter_principal = principal
    from browser_csrf import browser_csrf_denial

    denial = browser_csrf_denial(request, principal)
    if denial is not None:
        return denial
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
        elif request.url.path in _PRIVILEGED_STATE_PATHS:
            if request.headers.get("X-Requested-With") != "XMLHttpRequest":
                return JSONResponse(
                    {"error": "CSRF check failed: missing X-Requested-With header"},
                    status_code=403,
                )
    return await call_next(request)


async def add_security_headers(request: Request, call_next: Any) -> Any:
    """Inject standard security headers on every response (issue #7, #18)."""
    response = await call_next(request)
    if response.headers.get("content-type", "").partition(";")[0].strip().casefold() == "text/event-stream":
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["Cache-Control"] = "no-cache, no-transform"
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
        log.warning(
            "auth_rate_limit: IP %s exceeded %d attempts in %ds",
            ip,
            _AUTH_RATE_LIMIT,
            _AUTH_RATE_WINDOW,
        )
        raise HTTPException(
            status_code=429,
            detail="Too many authentication attempts. Please wait 5 minutes before retrying.",
            headers={"Retry-After": str(_AUTH_RATE_WINDOW)},
        )
    window.append(now)
    _auth_rate_store[ip] = window
