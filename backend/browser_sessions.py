"""Select a single, isolated cookie policy for each ingress plane."""

from __future__ import annotations

import hashlib
import hmac
from typing import Literal

from browser_origin import external_browser_request
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

_BROWSER_MAX_AGE_SECONDS = 15 * 60


class BrowserSessionMiddleware:
    """Preserve local sessions while refusing their cookies at browser ingress.

    Edge headers and the configured public host select stricter cookie handling;
    they never establish identity. Access/OAuth and route guards still apply.
    Separate signing domains prevent renaming a local cookie to the public name.
    """

    def __init__(
        self,
        app: ASGIApp,
        secret_key: str,
        session_cookie: str = "dashboard_session",
        max_age: int = 86400,
        same_site: Literal["lax", "strict", "none"] = "strict",
        https_only: bool = False,
    ) -> None:
        self.app = app
        self.local = SessionMiddleware(
            app, secret_key, session_cookie=session_cookie, max_age=max_age, same_site=same_site, https_only=https_only
        )
        browser_key = hmac.new(secret_key.encode(), b"runner-dashboard:browser-session:v1", hashlib.sha256).hexdigest()
        self.browser = SessionMiddleware(
            app,
            browser_key,
            session_cookie="__Host-rd_session",
            max_age=_BROWSER_MAX_AGE_SECONDS,
            same_site="lax",
            https_only=True,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        external = external_browser_request(HTTPConnection(scope))
        middleware = self.browser if external else self.local
        await middleware(scope, receive, send)
