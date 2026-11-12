"""Request-scoped context variables for structured logging and correlation.

Issue #331 — structured JSON logs with request_id correlation.

This module provides:
- ``request_id_ctx`` — a ContextVar holding the current request ID
- ``principal_id_ctx`` — a ContextVar holding the authenticated principal
- ``RequestIdMiddleware`` — ASGI/Starlette middleware that generates or
  propagates a ``request_id`` per HTTP request, stores it in the ContextVar
  and ``request.state``, and echoes it in ``X-Request-ID`` response header
- ``RequestIdLogFilter`` — a ``logging.Filter`` that injects ``request_id``
  and ``principal_id`` into every log record
- ``configure_json_logging`` — switches root logger to JSON output when
  ``LOG_FORMAT=json`` is set
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Context variables
# ---------------------------------------------------------------------------

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
principal_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("principal_id", default="-")
run_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")


def _new_request_id() -> str:
    """Generate a compact 12-hex-char request ID (48 bits of randomness)."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject a unique ``request_id`` into every HTTP request lifecycle.

    - Reads inbound ``X-Request-ID`` header; uses it if present, otherwise
      generates a new UUID-based ID.
    - Stores the ID in ``request.state.request_id`` and in ``request_id_ctx``
      so background coroutines spawned during the request inherit it.
    - Appends ``X-Request-ID`` to every response.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        rid = request.headers.get("X-Request-ID") or _new_request_id()
        token = request_id_ctx.set(rid)
        request.state.request_id = rid

        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)

        response.headers["X-Request-ID"] = rid
        return response


# ---------------------------------------------------------------------------
# Log filter
# ---------------------------------------------------------------------------


class RequestIdLogFilter(logging.Filter):
    """Inject ``request_id`` and ``principal_id`` into every log record.

    Attach to the root logger or any handler so that every log line emitted
    during an HTTP request carries the correlation IDs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()  # type: ignore[attr-defined]
        record.principal_id = principal_id_ctx.get()  # type: ignore[attr-defined]
        record.run_id = run_id_ctx.get()  # type: ignore[attr-defined]
        return True


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object.

    Required keys per issue #331 AC-4:
      ts, level, module, msg, request_id, principal_id, path
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt=None),
            "level": record.levelname,
            "module": record.module,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "principal_id": getattr(record, "principal_id", "-"),
            "run_id": getattr(record, "run_id", "-"),
            "path": getattr(record, "pathname", ""),
        }
        if record.exc_info:
            from observability_hygiene import format_exception_for_log  # noqa: PLC0415

            payload["exc"] = format_exception_for_log(*record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Public configuration helper
# ---------------------------------------------------------------------------


def configure_json_logging() -> None:
    """Switch the root logger to JSON output when ``LOG_FORMAT=json``.

    Also attaches ``RequestIdLogFilter`` to every handler so request_id
    flows into all log records regardless of format.

    Call this once at application startup *after* ``logging.basicConfig``.
    """
    from observability_hygiene import install_observability_hygiene  # noqa: PLC0415

    log_format = os.environ.get("LOG_FORMAT", "").strip().lower()
    root_logger = logging.getLogger()

    install_observability_hygiene(root_logger)

    # Always install the filter so request_id is available.
    _filter = RequestIdLogFilter()
    for handler in root_logger.handlers:
        if not any(isinstance(f, RequestIdLogFilter) for f in handler.filters):
            handler.addFilter(_filter)

    if log_format == "json":
        json_fmt = _JsonFormatter()
        for handler in root_logger.handlers:
            handler.setFormatter(json_fmt)

    # Also filter the "dashboard" logger hierarchy.
    dashboard_log = logging.getLogger("dashboard")
    if not any(isinstance(f, RequestIdLogFilter) for f in dashboard_log.filters):
        dashboard_log.addFilter(_filter)


# ---------------------------------------------------------------------------
# Convenience accessor
# ---------------------------------------------------------------------------


def current_request_id() -> str:
    """Return the active request ID, or ``"-"`` outside a request context."""
    return request_id_ctx.get()


def current_run_id() -> str:
    """Return the active staff run ID, or ``"-"`` outside a staff run context."""
    return run_id_ctx.get()
