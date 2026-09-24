"""Client error reporting router (issue #1292).

Receives frontend crash / render error reports from TabErrorBoundary and records
them into the ring-buffered FleetEvent store so crashes are visible in the
event log without requiring manual operator bug filings.

Rate-limited to prevent log flooding when a component repeatedly crashes.
"""

from __future__ import annotations

import logging
import threading
import time

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from fleet_events import EventStore, FleetEvent, get_event_store
from pydantic import BaseModel, Field

log = logging.getLogger("dashboard.client_errors")


class ClientErrorPayload(BaseModel):
    """Payload sent by TabErrorBoundary when an error is caught."""

    page: str = Field(..., min_length=1, max_length=200, description="Tab or page name")
    message: str = Field(..., min_length=1, max_length=2000, description="Error message")
    stack: str | None = Field(default=None, max_length=10000, description="Error stack trace")
    build_sha: str | None = Field(default=None, max_length=64, description="Build ID or git SHA")
    component: str | None = Field(default=None, max_length=200, description="Component name")


class ClientErrorLimiter:
    """Sliding-window in-memory rate limiter for client error submissions."""

    def __init__(self, max_per_minute: int = 30) -> None:
        self.max_per_minute = max_per_minute
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """Return True if under the limit; record the timestamp. Return False if limit reached."""
        now = time.time()
        cutoff = now - 60.0
        with self._lock:
            # Evict timestamps older than 60s
            self._timestamps = [t for t in self._timestamps if t >= cutoff]
            if len(self._timestamps) >= self.max_per_minute:
                return False
            self._timestamps.append(now)
            return True


_GLOBAL_LIMITER = ClientErrorLimiter(max_per_minute=30)


def create_client_errors_router(
    store: EventStore | None = None,
    limiter: ClientErrorLimiter | None = None,
) -> APIRouter:
    """Construct the client-errors router with injectable dependencies."""
    r = APIRouter(tags=["client-errors"])

    @r.post("/api/client-errors")
    async def post_client_error(payload: ClientErrorPayload) -> JSONResponse:
        active_limiter = limiter if limiter is not None else _GLOBAL_LIMITER
        active_store = store if store is not None else get_event_store()

        if not active_limiter.allow():
            log.warning(
                "Client error report rate limited: page=%s msg=%s",
                payload.page,
                payload.message[:80],
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "status": "rate_limited",
                    "detail": "Rate limit exceeded for client error reporting",
                },
            )

        now_ms = int(time.time() * 1000)
        short_msg = payload.message.strip().splitlines()[0][:80]
        event = FleetEvent(
            ts=now_ms,
            severity="critical",
            kind="client_error",
            title=f"Client error on {payload.page}: {short_msg}",
            detail=(
                f"Page: {payload.page}\n"
                f"Message: {payload.message}\n"
                f"Build SHA: {payload.build_sha or 'unknown'}\n"
                f"Component: {payload.component or payload.page}\n"
                f"Stack:\n{payload.stack or 'none'}"
            ),
            node=None,
        )
        active_store.record(event)
        log.error("Recorded client error on %s: %s", payload.page, payload.message)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "recorded", "event_ts": now_ms},
        )

    return r


router = create_client_errors_router()
