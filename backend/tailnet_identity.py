"""Phone sign-in over the tailnet via Tailscale identity headers (issue #1755).

Problem: with ``tailscale serve --bg http://localhost:8321``, uvicorn's
proxy-header handling rewrites the resolved client to the phone's tailnet
address, so the loopback-admin bypass in ``identity.py`` correctly refuses
it — and no other principal exists for a phone with no configured OAuth app.

This module admits a request as a human principal, with exactly the power of
the local loopback principal, when ALL of the following hold:

  1. ``DASHBOARD_TAILSCALE_AUTH=1`` is set.
  2. The *raw transport peer* (recorded by :class:`TransportPeerMiddleware`
     before any proxy-header rewriting) is loopback — i.e. the request
     physically arrived from the local ``tailscaled`` process, not directly
     from the network.
  3. The *resolved* client address (after proxy-header rewriting) is in a
     Tailscale CGNAT range.
  4. The ``Tailscale-User-Login`` header names a login in the
     ``DASHBOARD_TAILSCALE_LOGINS`` allow-list.

A direct tailnet caller who forges the ``Tailscale-User-Login`` header is
refused by condition 2: their raw transport peer is their own tailnet
address, never loopback, because only ``tailscaled`` on this host can
connect via loopback and inject those headers.

Approvals: the loopback power includes ``staff.approve``, so the owner approves
from any device (#1786). The ``tailnet-approver`` role (#1770) is still added for
a sign-in from *another* tailnet device, i.e. not one of
``DASHBOARD_TAILSCALE_SELF_IPS``, so the audit trail can tell a phone from Desk.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import Any, cast

from fastapi import Request
from identity import Principal
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("dashboard")

# Tailscale's CGNAT IPv4 range and ULA IPv6 range for tailnet addresses.
_TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")

_TRANSPORT_PEER_SCOPE_KEY = "rd.transport_peer"

TAILNET_APPROVER_ROLE = "tailnet-approver"


class TransportPeerMiddleware:
    """Pure ASGI middleware that records the raw transport peer.

    Must be installed *outside* ``ProxyHeadersMiddleware`` so it observes the
    real socket peer before any ``X-Forwarded-For`` rewriting overwrites
    ``scope["client"]``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") in ("http", "websocket"):
            client = scope.get("client")
            scope[_TRANSPORT_PEER_SCOPE_KEY] = client[0] if client else None
        await self.app(scope, receive, send)


def build_asgi_app(app: ASGIApp) -> ASGIApp:
    """Wrap ``app`` with the transport-peer recorder and uvicorn's proxy-header
    middleware, trusting only the local proxy (``tailscale serve``) on loopback.
    """
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    # uvicorn types ASGI apps with its own TypedDict scopes; starlette uses plain
    # mappings. Both describe the same ASGI callable, so bridge them with casts.
    proxied = ProxyHeadersMiddleware(cast(Any, app), trusted_hosts=["127.0.0.1", "::1"])
    return TransportPeerMiddleware(cast(ASGIApp, proxied))


def _tailscale_auth_enabled() -> bool:
    return os.environ.get("DASHBOARD_TAILSCALE_AUTH") == "1"


def _is_loopback_addr(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_tailnet_addr(host: str | None) -> bool:
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr in _TAILNET_V4 or addr in _TAILNET_V6


def _allowed_logins() -> set[str]:
    raw = os.environ.get("DASHBOARD_TAILSCALE_LOGINS", "")
    return {login.strip().lower() for login in raw.split(",") if login.strip()}


def _self_tailnet_ips() -> set[str] | None:
    """This host's own tailnet addresses, or ``None`` when not configured."""
    raw = os.environ.get("DASHBOARD_TAILSCALE_SELF_IPS", "")
    ips: set[str] = set()
    for part in raw.split(","):
        try:
            ips.add(str(ipaddress.ip_address(part.strip())))
        except ValueError:
            continue
    return ips or None


def _from_other_tailnet_device(resolved_host: str) -> bool:
    """True only when ``resolved_host`` is known not to be this host (fail closed)."""
    self_ips = _self_tailnet_ips()
    if self_ips is None:
        return False
    return str(ipaddress.ip_address(resolved_host)) not in self_ips


def tailnet_principal(request: Request) -> Principal | None:
    """Resolve a Tailscale-identity principal, or ``None`` when any admission
    condition fails.

    Same power as the local loopback principal (``roles=["loopback"]``), plus the
    ``tailnet-approver`` marker role for a sign-in from another tailnet device (#1770).

    Post: ``None``, or a human principal whose roles are ``["loopback"]`` or
    ``["loopback", "tailnet-approver"]``.
    """
    if not _tailscale_auth_enabled():
        return None

    scope = getattr(request, "scope", None)
    transport_peer = scope.get(_TRANSPORT_PEER_SCOPE_KEY) if isinstance(scope, dict) else None
    if not _is_loopback_addr(transport_peer):
        return None

    client = getattr(request, "client", None)
    resolved_host = getattr(client, "host", None) if client is not None else None
    if not _is_tailnet_addr(resolved_host):
        return None

    login_header = request.headers.get("Tailscale-User-Login")
    if not login_header:
        return None
    login = login_header.strip()
    if not login or login.lower() not in _allowed_logins():
        return None

    name_header = request.headers.get("Tailscale-User-Name")
    name = name_header.strip() if name_header and name_header.strip() else login

    log.debug("tailnet auth admitted login=%s", login)

    roles = ["loopback"]
    if _from_other_tailnet_device(str(resolved_host)):
        roles.append(TAILNET_APPROVER_ROLE)

    return Principal(
        id=f"tailscale:{login}",
        type="human",
        name=name,
        roles=roles,
    )
