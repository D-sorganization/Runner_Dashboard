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
  4. The ``Tailscale-User-Login`` header (or, with
     ``DASHBOARD_TAILSCALE_DIRECT_ALLOW``, the first configured login) names
     a login in the ``DASHBOARD_TAILSCALE_LOGINS`` allow-list.
  5. Tailscaled's own LocalAPI ``whois`` record for this exact peer/port
     (#3011) names the *same* login. Only tailscaled — proxying a real
     ``tailscale serve`` request or a genuine direct tailnet socket — has
     such a record; an unrelated local process that merely connects to this
     port on loopback and forges the headers above has none, and is refused.

Condition 2 alone is not proof against a local attacker: any process on this
host, not only ``tailscaled``, can open a loopback connection and send the
same forged headers. Condition 5 closes that gap by asking the one party who
cannot be impersonated — tailscaled itself — who really owns the connection.

Approvals (#1770, #3540): ``LOOPBACK_SCOPES`` does not include ``staff.approve``;
the ``tailnet-approver`` role (#1770) grants ``staff.approve`` for a sign-in
from *another* tailnet device, i.e. not one of ``DASHBOARD_TAILSCALE_SELF_IPS``.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import os
import socket
from dataclasses import dataclass
from typing import Any, cast

from fastapi import Request
from identity import Principal
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("dashboard")

# Tailscale's CGNAT IPv4 range and ULA IPv6 range for tailnet addresses.
_TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")

_TRANSPORT_PEER_SCOPE_KEY = "rd.transport_peer"
_TRANSPORT_PEER_PORT_SCOPE_KEY = "rd.transport_peer_port"

TAILNET_APPROVER_ROLE = "tailnet-approver"

_DEFAULT_TAILSCALED_SOCKET = "/var/run/tailscale/tailscaled.sock"
_WHOIS_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class WhoIsIdentity:
    """A login verified by tailscaled's own record of a connection (#3011)."""

    login: str
    name: str


class _UnixSocketHTTPConnection(http.client.HTTPConnection):
    """An ``HTTPConnection`` over an ``AF_UNIX`` socket (tailscaled's LocalAPI)."""

    def __init__(self, socket_path: str, timeout: float = _WHOIS_TIMEOUT_SECONDS) -> None:
        super().__init__("local-tailscaled.sock", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


def _tailscaled_socket_path() -> str:
    return os.environ.get("DASHBOARD_TAILSCALED_SOCKET", _DEFAULT_TAILSCALED_SOCKET)


def _tailscaled_whois(host: str | None, port: int | None) -> WhoIsIdentity | None:
    """Ask tailscaled's LocalAPI who owns the connection seen at ``host:port``.

    Only ``tailscaled`` itself — proxying a real ``tailscale serve`` request or
    a direct tailnet socket — has a WhoIs record for that exact peer/port. An
    unrelated local process spoofing identity headers over its own loopback
    connection has none, so this fails closed (``None``) on any lookup
    failure, timeout or malformed response, not just a clean "not found".
    """
    if not host or not port:
        return None
    try:
        conn = _UnixSocketHTTPConnection(_tailscaled_socket_path())
        try:
            conn.request("GET", f"/localapi/v0/whois?addr={host}:{port}")
            resp = conn.getresponse()
            body = resp.read()
            if resp.status != 200:
                return None
            data = json.loads(body)
        finally:
            conn.close()
    except (OSError, ValueError, http.client.HTTPException):
        return None
    profile = data.get("UserProfile") if isinstance(data, dict) else None
    login = profile.get("LoginName") if isinstance(profile, dict) else None
    if not isinstance(login, str) or not login.strip():
        return None
    display = profile.get("DisplayName") if isinstance(profile, dict) else None
    name = display.strip() if isinstance(display, str) and display.strip() else login.strip()
    return WhoIsIdentity(login=login.strip(), name=name)


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
            scope[_TRANSPORT_PEER_PORT_SCOPE_KEY] = client[1] if client else None
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

    direct_allow = os.getenv("DASHBOARD_TAILSCALE_DIRECT_ALLOW", "0").strip().lower() in ("1", "true", "yes")
    scope = getattr(request, "scope", None)
    transport_peer = scope.get(_TRANSPORT_PEER_SCOPE_KEY) if isinstance(scope, dict) else None
    transport_port = scope.get(_TRANSPORT_PEER_PORT_SCOPE_KEY) if isinstance(scope, dict) else None
    is_loopback_peer = _is_loopback_addr(transport_peer)
    if not is_loopback_peer and not (direct_allow and _is_tailnet_addr(transport_peer)):
        return None

    client = getattr(request, "client", None)
    resolved_host = getattr(client, "host", None) if client is not None else None
    if not _is_tailnet_addr(resolved_host):
        return None

    login_header = request.headers.get("Tailscale-User-Login") if is_loopback_peer else None
    allowed = _allowed_logins()
    if not login_header:
        if direct_allow and allowed:
            raw_logins = [
                part.strip() for part in os.getenv("DASHBOARD_TAILSCALE_LOGINS", "").split(",") if part.strip()
            ]
            login = raw_logins[0] if raw_logins else sorted(allowed)[0]
        else:
            return None
    else:
        login = login_header.strip()
        if not login or login.lower() not in allowed:
            return None

    # #3011: a header (or a blindly-picked first allow-listed login) is not
    # proof of identity — any local process can forge it over its own
    # loopback connection. Require tailscaled's own WhoIs record for this
    # exact peer/port to confirm the same login before admitting.
    verified = _tailscaled_whois(transport_peer, transport_port)
    if verified is None or verified.login.lower() != login.lower():
        return None

    name_header = request.headers.get("Tailscale-User-Name")
    name = name_header.strip() if name_header and name_header.strip() else login

    log.debug("tailnet auth admitted login=%s", login)

    roles = ["loopback"]
    if _from_other_tailnet_device(str(resolved_host)):
        roles.append(TAILNET_APPROVER_ROLE)
    if os.getenv("DASHBOARD_TAILSCALE_OPERATOR_WRITES", "0").strip().lower() in ("1", "true", "yes") or os.getenv(
        "DASHBOARD_LOOPBACK_OPERATOR_WRITES", "0"
    ).strip().lower() in ("1", "true", "yes"):
        for extra_role in ("operator", "admin"):
            if extra_role not in roles:
                roles.append(extra_role)

    return Principal(
        id=f"tailscale:{login}",
        type="human",
        name=name,
        roles=roles,
    )
