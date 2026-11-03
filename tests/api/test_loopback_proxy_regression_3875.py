"""Loopback auth vs trusted reverse proxies (#3875, epic #3874).

Paperclip panel residual (W1c): when ``DASHBOARD_LOOPBACK_AUTH=1``, document which
proxy peers may rewrite ``scope["client"]`` and when the synthetic ``__loopback__``
principal is granted.

Production contract (``backend/tailnet_identity.build_asgi_app``):

- ``TransportPeerMiddleware`` records the raw socket peer in
  ``scope["rd.transport_peer"]`` *before* proxy-header rewriting.
- ``ProxyHeadersMiddleware`` trusts **only** loopback proxy peers
  (``127.0.0.1`` and ``::1``) for ``X-Forwarded-For`` / related headers.
- ``identity._is_loopback_request`` uses ``request.client.host`` — the resolved
  client after rewriting — never forwarded headers in isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import identity as _identity  # noqa: E402
import tailnet_identity as _tailnet_identity  # noqa: E402
from identity import Principal, require_principal  # noqa: E402
from tailnet_identity import WhoIsIdentity, build_asgi_app  # noqa: E402

_LOOPBACK_PEER = ("127.0.0.1", 54321)
_REMOTE_PEER = ("203.0.113.50", 54321)
_TAILNET_PHONE = "100.64.0.4"
_LOGIN = "alice@example.com"


def test_build_asgi_app_trusted_proxy_hosts_are_loopback_only() -> None:
    """Only local reverse proxies (tailscale serve, mirrored-port helper) may rewrite XFF."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app = FastAPI()

    async def _noop(scope, receive, send):
        pass

    wrapped = build_asgi_app(app)
    # Walk the middleware onion to the ProxyHeadersMiddleware instance.
    current = wrapped
    trusted: list[str] | None = None
    for _ in range(6):
        if isinstance(current, _tailnet_identity.TransportPeerMiddleware):
            current = current.app  # type: ignore[attr-defined]
            continue
        if isinstance(current, ProxyHeadersMiddleware):
            trusted = sorted(str(host) for host in current.trusted_hosts.trusted_hosts)
            break
        current = getattr(current, "app", None)
        if current is None:
            break
    assert trusted == ["127.0.0.1", "::1"]


@pytest.fixture
def loopback_auth_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    monkeypatch.delenv("DASHBOARD_TAILSCALE_AUTH", raising=False)

    app = FastAPI()
    dep = Depends(require_principal)

    @app.get("/whoami")
    def whoami(principal: Principal = dep) -> dict[str, str]:
        return {"id": principal.id}

    return app


async def test_direct_loopback_peer_grants_loopback_principal(loopback_auth_app: FastAPI) -> None:
    wrapped = build_asgi_app(loopback_auth_app)
    async with AsyncClient(
        transport=ASGITransport(app=wrapped, client=_LOOPBACK_PEER),
        base_url="http://testserver",
    ) as ac:
        resp = await ac.get("/whoami")
    assert resp.status_code == 200
    assert resp.json()["id"] == "__loopback__"


async def test_remote_peer_with_spoofed_loopback_xff_is_rejected(loopback_auth_app: FastAPI) -> None:
    wrapped = build_asgi_app(loopback_auth_app)
    async with AsyncClient(
        transport=ASGITransport(app=wrapped, client=_REMOTE_PEER),
        base_url="http://testserver",
    ) as ac:
        resp = await ac.get("/whoami", headers={"X-Forwarded-For": "127.0.0.1"})
    assert resp.status_code == 401


async def test_trusted_loopback_proxy_rewriting_xff_does_not_grant_loopback_principal(
    loopback_auth_app: FastAPI,
) -> None:
    """Tailscale serve shape: transport peer is loopback but resolved client is tailnet."""
    wrapped = build_asgi_app(loopback_auth_app)
    async with AsyncClient(
        transport=ASGITransport(app=wrapped, client=_LOOPBACK_PEER),
        base_url="http://testserver",
    ) as ac:
        resp = await ac.get("/whoami", headers={"X-Forwarded-For": _TAILNET_PHONE})
    assert resp.status_code == 401


async def test_untrusted_remote_proxy_does_not_apply_xff_to_client(loopback_auth_app: FastAPI) -> None:
    """Non-loopback proxies are not trusted — scope client stays the remote peer."""
    captured: dict[str, object] = {}

    app = FastAPI()

    async def inner(scope, receive, send):
        captured["client"] = scope.get("client")
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = build_asgi_app(inner)
    async with AsyncClient(
        transport=ASGITransport(app=wrapped, client=_REMOTE_PEER),
        base_url="http://testserver",
    ) as ac:
        await ac.get("/", headers={"X-Forwarded-For": _TAILNET_PHONE})
    assert captured["client"][0] == _REMOTE_PEER[0]


async def test_trusted_ipv6_loopback_proxy_may_rewrite_xff(loopback_auth_app: FastAPI) -> None:
    captured: dict[str, object] = {}

    async def inner(scope, receive, send):
        captured["client"] = scope.get("client")
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = build_asgi_app(inner)
    async with AsyncClient(
        transport=ASGITransport(app=wrapped, client=("::1", 54321)),
        base_url="http://testserver",
    ) as ac:
        await ac.get("/", headers={"X-Forwarded-For": _TAILNET_PHONE})
    assert captured["client"][0] == _TAILNET_PHONE


def test_require_principal_uses_resolved_client_not_transport_peer_for_loopback_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when transport peer is loopback, a rewritten tailnet client must not get __loopback__."""
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    from unittest.mock import MagicMock

    req = MagicMock()
    req.client.host = _TAILNET_PHONE
    req.scope = {"rd.transport_peer": "127.0.0.1", "rd.transport_peer_port": 54321}
    req.headers.get.return_value = None
    req.state = MagicMock()
    if hasattr(req, "session"):
        del req.session

    with pytest.raises(HTTPException) as exc_info:
        require_principal(request=req, header_token=None, cookie_token=None)
    assert exc_info.value.status_code == 401


async def test_tailscale_serve_path_admits_tailnet_not_loopback_when_flags_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When tailnet auth is enabled, proxied phones use tailnet identity — not __loopback__."""
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)

    def _fake_whois(host: str | None, port: int | None) -> WhoIsIdentity | None:
        if host == "127.0.0.1" and port == _LOOPBACK_PEER[1]:
            return WhoIsIdentity(login=_LOGIN, name="Alice")
        return None

    monkeypatch.setattr(_tailnet_identity, "_tailscaled_whois", _fake_whois)

    app = FastAPI()
    dep = Depends(require_principal)

    @app.get("/whoami")
    def whoami(principal: Principal = dep) -> dict[str, str]:
        return {"id": principal.id}

    wrapped = build_asgi_app(app)
    async with AsyncClient(
        transport=ASGITransport(app=wrapped, client=_LOOPBACK_PEER),
        base_url="http://testserver",
    ) as ac:
        resp = await ac.get(
            "/whoami",
            headers={"X-Forwarded-For": _TAILNET_PHONE, "Tailscale-User-Login": _LOGIN},
        )
    assert resp.status_code == 200
    assert resp.json()["id"] == f"tailscale:{_LOGIN}"
    assert resp.json()["id"] != "__loopback__"


def test_loopback_auth_disabled_denies_bare_loopback_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_LOOPBACK_AUTH", raising=False)
    from unittest.mock import MagicMock

    req = MagicMock()
    req.client.host = "127.0.0.1"
    req.scope = {"rd.transport_peer": "127.0.0.1"}
    req.headers.get.return_value = None
    req.state = MagicMock()
    if hasattr(req, "session"):
        del req.session

    with pytest.raises(HTTPException) as exc_info:
        require_principal(request=req, header_token=None, cookie_token=None)
    assert exc_info.value.status_code == 401


def test_loopback_principal_scopes_exclude_privileged_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: loopback synthetic principal stays read/agent scoped (#3540)."""
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    prin = _identity._loopback_principal()
    assert not _identity.principal_has_scope(prin, "staff.dispatch")
    assert not _identity.principal_has_scope(prin, "runners.control")
    assert _identity.principal_has_scope(prin, "fleet.read")
