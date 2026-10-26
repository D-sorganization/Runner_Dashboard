"""Build the request Host allowlist from dashboard public origins."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from oauth_config import allowed_public_origins
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
_IPV6_LOOPBACK_HOST = re.compile(r"\[::1\](?::\d{1,5})?\Z", re.IGNORECASE)


class IPv6AwareTrustedHostMiddleware:
    """Apply Starlette Host checks while handling bracketed IPv6 loopback."""

    def __init__(self, app: ASGIApp, *, allowed_hosts: tuple[str, ...]) -> None:
        self._trusted = TrustedHostMiddleware(app, allowed_hosts=allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            headers = list(scope.get("headers", []))
            for index, (name, value) in enumerate(headers):
                if name.lower() == b"host" and _IPV6_LOOPBACK_HOST.fullmatch(value.decode("latin-1")):
                    # Starlette's middleware splits Host on ':' and otherwise
                    # misreads bracketed IPv6 literals. Normalize this exact
                    # loopback authority to its already trusted local alias.
                    headers[index] = (name, b"localhost")
                    scope = {**scope, "headers": headers}
                    break
        await self._trusted(scope, receive, send)


def trusted_hosts_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    registry: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    """Return local hosts plus hostnames from configured public origins.

    The returned collection is suitable for Starlette's TrustedHostMiddleware;
    URL schemes and ports are intentionally excluded because Host validation
    compares hostnames.
    """
    source = os.environ if environ is None else environ
    hosts = set(_LOCAL_HOSTS)
    origins = set(allowed_public_origins(source, registry=registry))
    public_origin = source.get("DASHBOARD_PUBLIC_ORIGIN", "").strip()
    if public_origin:
        origins.add(public_origin)
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.hostname:
            hosts.add(parsed.hostname.casefold())
    return tuple(sorted(hosts))
