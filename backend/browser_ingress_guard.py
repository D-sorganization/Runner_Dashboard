"""Fail-closed Cloudflare Access perimeter for external human browser requests."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import uuid
from collections.abc import Callable
from typing import Any

from browser_origin import browser_origin, external_browser_request
from browser_route_policy import classify_browser_route
from fastapi import Request
from fastapi.responses import JSONResponse
from identity import Principal, identity_manager
from staff.rate_limit import BrowserRateLimiter
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("dashboard.browser_ingress")
_CANONICAL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_OWNER_ID = re.compile(r"[1-9][0-9]{0,19}\Z")


class BrowserIngressMiddleware:
    """Validate Access and the owner browser session before external handlers."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        validator: Any | None = None,
        enabled: bool = True,
        owner_github_id: str | None = None,
        principal_lookup: Callable[[str], Principal | None] | None = None,
        session_is_active: Callable[[str], bool] | None = None,
        rate_limiter: BrowserRateLimiter | None = None,
    ) -> None:
        self.app = app
        self.validator = validator
        self.enabled = enabled
        self.owner_github_id = owner_github_id
        self.principal_lookup = principal_lookup or identity_manager.get_principal
        self.session_is_active = session_is_active
        self.rate_limiter = rate_limiter or BrowserRateLimiter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        if not external_browser_request(connection):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return

        request = Request(scope, receive)
        if not self._feature_enabled() or browser_origin() is None:
            await self._deny(scope, receive, send, "browser_ingress_disabled")
            return
        if request.headers.get("authorization", "").partition(" ")[0].casefold() == "bearer":
            await self._deny(scope, receive, send, "machine_bearer_rejected")
            return

        policy = classify_browser_route(request.method, request.url.path)
        if policy is None:
            await self._deny(scope, receive, send, "browser_route_not_allowlisted")
            return
        token = request.headers.get("cf-access-jwt-assertion", "")
        if not token or not await self._access_valid(scope, token):
            await self._deny(scope, receive, send, "access_assertion_rejected")
            return

        if policy.access in {"shell", "access"}:
            await self.app(scope, receive, send)
            return

        principal, owner_id, missing_session = await self._owner_session(request)
        if missing_session:
            await self._deny(scope, receive, send, "owner_session_missing", status_code=401)
            return
        if principal is None or owner_id is None:
            await self._deny(scope, receive, send, "owner_session_mismatch")
            return
        request.state.perimeter_principal = principal

        if policy.access == "auth_mutation" and not self._same_origin_mutation(request):
            await self._deny(scope, receive, send, "auth_mutation_origin_rejected")
            return
        if policy.access in {"messages", "dispatches"}:
            app = scope.get("app")
            csrf_check = getattr(getattr(app, "state", None), "browser_csrf_check", None)
            if csrf_check is None:
                from browser_csrf import browser_csrf_denial

                csrf_check = browser_csrf_denial
            denial = csrf_check(request, principal)
            if denial is not None:
                await denial(scope, receive, send)
                return
            thread_id = self._thread_id(request.url.path)
            allowed, retry_after = self.rate_limiter.acquire(owner_id, thread_id, policy.access)
            if not allowed:
                retry = max(1, math.ceil(retry_after))
                await JSONResponse(
                    {"error": "Request not permitted"},
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _feature_enabled(self) -> bool:
        return self.enabled is True and os.environ.get("DASHBOARD_BROWSER_INGRESS_ENABLED", "").casefold() in {
            "1",
            "true",
        }

    async def _access_valid(self, scope: Scope, token: str) -> bool:
        app = scope.get("app")
        validator = getattr(getattr(app, "state", None), "browser_access_validator", None) or self.validator
        if validator is None:
            return False
        try:
            await asyncio.to_thread(validator.validate, token)
            return True
        except Exception:  # noqa: BLE001 - untrusted edge assertions fail closed.
            return False

    async def _owner_session(self, request: Request) -> tuple[Principal | None, str | None, bool]:
        session = request.session
        session_id = session.get("session_id")
        principal_id = session.get("principal_id")
        if not isinstance(session_id, str) or not session_id or not isinstance(principal_id, str) or not principal_id:
            return None, None, True
        active = self.session_is_active
        if active is None:
            import session_management

            active = session_management.is_session_active
        if not await asyncio.to_thread(active, session_id):
            return None, None, True
        principal = self.principal_lookup(principal_id)
        owner_id = session.get("github_user_id")
        configured = self.owner_github_id or os.environ.get("DASHBOARD_OWNER_GITHUB_ID", "")
        if (
            principal is None
            or getattr(principal, "id", None) != principal_id
            or getattr(principal, "type", None) != "human"
            or not isinstance(configured, str)
            or not _OWNER_ID.fullmatch(configured)
            or not isinstance(owner_id, str)
            or not _OWNER_ID.fullmatch(owner_id)
            or owner_id != configured
        ):
            return None, None, False
        return principal, owner_id, False

    @staticmethod
    def _same_origin_mutation(request: Request) -> bool:
        expected = browser_origin()
        return bool(
            expected
            and request.headers.get("origin") == expected
            and request.headers.get("x-requested-with") == "XMLHttpRequest"
        )

    @staticmethod
    def _thread_id(path: str) -> str:
        parts = path.split("/")
        if len(parts) > 5 and parts[4] == "threads" and _CANONICAL_ID.fullmatch(parts[5]):
            return parts[5]
        if len(parts) > 5 and parts[4] == "panels" and _CANONICAL_ID.fullmatch(parts[5]):
            return parts[5]
        return "_new"

    async def _deny(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        reason: str,
        *,
        status_code: int = 403,
    ) -> None:
        request = Request(scope, receive)
        request_id = uuid.uuid4().hex[:12]
        client_host = request.client.host if request.client else "unknown"
        log.warning(
            "external browser request denied",
            extra={
                "request_id": request_id,
                "source_ip": client_host,
                "cf_ray": request.headers.get("cf-ray"),
                "path": request.url.path,
                "method": request.method,
                "reason": reason,
            },
        )
        await JSONResponse(
            {"error": "Request not permitted"},
            status_code=status_code,
            headers={"X-Request-ID": request_id},
        )(scope, receive, send)
