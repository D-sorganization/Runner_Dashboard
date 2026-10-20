"""Process-local HTTP admission control for graceful dashboard drains."""

from __future__ import annotations

from collections.abc import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD"})


class DrainAdmissionMiddleware:
    """Reject new mutations while draining, preserving diagnostics and admitted work."""

    def __init__(self, app: ASGIApp, is_draining: Callable[[], bool]) -> None:
        self.app = app
        self.is_draining = is_draining

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.is_draining():
            method = scope["method"].upper()
            path = scope["path"]
            reject_mutation = method in _MUTATION_METHODS and path != "/_drain"
            reject_readiness = method in _READ_METHODS and path == "/readyz"
            if reject_mutation or reject_readiness:
                response = JSONResponse(
                    {"status": "draining", "detail": "server is draining"},
                    status_code=503,
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
