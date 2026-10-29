"""Tests for phone sign-in over the tailnet via Tailscale identity headers (#1755).

Covers ``backend/tailnet_identity.py`` and its wiring into
``backend/identity.py``:

- admitted when every condition holds
- refused when ``DASHBOARD_TAILSCALE_AUTH`` is unset
- refused when the login is not in the allow-list
- refused when a direct tailnet caller forges the identity headers (raw
  transport peer is not loopback)
- refused when the resolved client is outside the tailnet ranges
- a forwarded request with no Tailscale headers still gets 401
- plain loopback auth (``DASHBOARD_LOOPBACK_AUTH``) is unchanged
- ``TransportPeerMiddleware`` records the raw peer before
  ``ProxyHeadersMiddleware`` rewrites ``scope["client"]``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import identity as _identity  # noqa: E402
import tailnet_identity as _tailnet_identity  # noqa: E402
from identity import Principal, require_principal  # noqa: E402
from tailnet_identity import TransportPeerMiddleware, WhoIsIdentity, build_asgi_app, tailnet_principal  # noqa: E402

_LOOPBACK_PEER = ("127.0.0.1", 54321)
_TAILNET_CLIENT_IP = "100.64.0.4"
_NON_TAILNET_IP = "203.0.113.7"
_LOGIN = "alice@example.com"


def _mock_whois_matching(
    monkeypatch: pytest.MonkeyPatch,
    *,
    login: str,
    name: str | None = None,
    host: str = "127.0.0.1",
    port: int = 54321,
) -> None:
    """Make tailscaled's WhoIs confirm `login` for exactly `host:port` (#3011)."""

    def _fake(h: str | None, p: int | None) -> WhoIsIdentity | None:
        if h == host and p == port:
            return WhoIsIdentity(login=login, name=name or login)
        return None

    monkeypatch.setattr(_tailnet_identity, "_tailscaled_whois", _fake)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    """Ensure tailnet/loopback auth env vars start unset for every test."""
    monkeypatch.delenv("DASHBOARD_TAILSCALE_AUTH", raising=False)
    monkeypatch.delenv("DASHBOARD_TAILSCALE_LOGINS", raising=False)
    monkeypatch.delenv("DASHBOARD_LOOPBACK_AUTH", raising=False)
    monkeypatch.delenv("DASHBOARD_TAILSCALE_SELF_IPS", raising=False)
    yield


def _mock_request(
    *,
    transport_peer: str | None,
    client_host: str | None,
    headers: dict[str, str] | None = None,
    transport_port: int | None = 54321,
) -> MagicMock:
    req = MagicMock()
    req.scope = {"rd.transport_peer": transport_peer, "rd.transport_peer_port": transport_port}
    req.client = MagicMock()
    req.client.host = client_host
    headers = headers or {}
    req.headers = MagicMock()
    req.headers.get.side_effect = lambda name, default=None: headers.get(name, default)
    return req


# ---------------------------------------------------------------------------
# Unit tests: tailnet_principal
# ---------------------------------------------------------------------------


def test_tailnet_principal_admitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    _mock_whois_matching(monkeypatch, login=_LOGIN)
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=_TAILNET_CLIENT_IP,
        headers={"Tailscale-User-Login": _LOGIN, "Tailscale-User-Name": "Alice"},
    )
    prin = tailnet_principal(req)
    assert prin is not None
    assert prin.id == f"tailscale:{_LOGIN}"
    assert prin.type == "human"
    assert prin.name == "Alice"
    assert prin.roles == ["loopback"]


def test_tailnet_principal_admitted_name_falls_back_to_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    _mock_whois_matching(monkeypatch, login=_LOGIN, host="::1")
    req = _mock_request(
        transport_peer="::1",
        client_host="fd7a:115c:a1e0::1234",
        headers={"Tailscale-User-Login": _LOGIN},
    )
    prin = tailnet_principal(req)
    assert prin is not None
    assert prin.name == _LOGIN


def test_tailnet_principal_refused_flag_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refused when DASHBOARD_TAILSCALE_AUTH is unset, even with valid headers."""
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=_TAILNET_CLIENT_IP,
        headers={"Tailscale-User-Login": _LOGIN},
    )
    assert tailnet_principal(req) is None


def test_tailnet_principal_refused_login_not_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", "bob@example.com,carol@example.com")
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=_TAILNET_CLIENT_IP,
        headers={"Tailscale-User-Login": _LOGIN},
    )
    assert tailnet_principal(req) is None


def test_tailnet_principal_login_allowlist_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", "Alice@Example.com")
    _mock_whois_matching(monkeypatch, login=_LOGIN)
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=_TAILNET_CLIENT_IP,
        headers={"Tailscale-User-Login": "  alice@example.com  "},
    )
    prin = tailnet_principal(req)
    assert prin is not None
    assert prin.id == f"tailscale:{_LOGIN}"


def test_tailnet_principal_refused_raw_peer_not_loopback_forged_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A direct tailnet caller who forges the Tailscale headers is refused:
    their raw transport peer is their own tailnet address, never loopback.
    """
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    req = _mock_request(
        transport_peer=_TAILNET_CLIENT_IP,  # raw peer IS the tailnet caller, not loopback
        client_host=_TAILNET_CLIENT_IP,
        headers={"Tailscale-User-Login": _LOGIN},
    )
    assert tailnet_principal(req) is None


def test_tailnet_principal_refused_client_outside_tailnet_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=_NON_TAILNET_IP,
        headers={"Tailscale-User-Login": _LOGIN},
    )
    assert tailnet_principal(req) is None


def test_tailnet_principal_refused_missing_login_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """A forwarded request with no Tailscale headers is refused (caller falls
    through to 401 at require_principal)."""
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=_TAILNET_CLIENT_IP,
        headers={},
    )
    assert tailnet_principal(req) is None


# ---------------------------------------------------------------------------
# require_principal wiring
# ---------------------------------------------------------------------------


def test_require_principal_admits_tailnet_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    _mock_whois_matching(monkeypatch, login=_LOGIN)
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=_TAILNET_CLIENT_IP,
        headers={"Tailscale-User-Login": _LOGIN},
    )
    req.state = MagicMock()
    req.headers.get.side_effect = lambda name, default=None: (
        {"Tailscale-User-Login": _LOGIN}.get(name, default) if name != "X-Impersonate-Principal" else None
    )
    if hasattr(req, "session"):
        del req.session

    prin = require_principal(request=req, header_token=None, cookie_token=None)
    assert prin.id == f"tailscale:{_LOGIN}"
    assert "loopback" in prin.roles


def test_require_principal_still_401_without_any_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """A forwarded request with no Tailscale headers and no other credential
    still gets 401 (tailnet auth is additive, never opens the perimeter)."""
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=_TAILNET_CLIENT_IP,
        headers={},
    )
    req.state = MagicMock()
    if hasattr(req, "session"):
        del req.session

    with pytest.raises(HTTPException) as exc_info:
        require_principal(request=req, header_token=None, cookie_token=None)
    assert exc_info.value.status_code == 401


def test_require_principal_loopback_auth_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plain DASHBOARD_LOOPBACK_AUTH behaviour must be unaffected by #1755."""
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    req = MagicMock()
    req.client.host = "127.0.0.1"
    req.scope = {"rd.transport_peer": "127.0.0.1"}
    req.headers.get.return_value = None
    req.state = MagicMock()
    if hasattr(req, "session"):
        del req.session

    prin = require_principal(request=req, header_token=None, cookie_token=None)
    assert prin.id == "__loopback__"
    assert "loopback" in prin.roles


def test_require_principal_loopback_denied_when_neither_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
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


# ---------------------------------------------------------------------------
# _resolve_principal_optional wiring
# ---------------------------------------------------------------------------


def test_resolve_principal_optional_returns_tailnet_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    _mock_whois_matching(monkeypatch, login=_LOGIN)
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=_TAILNET_CLIENT_IP,
        headers={"Tailscale-User-Login": _LOGIN},
    )
    if hasattr(req, "session"):
        del req.session

    prin = _identity._resolve_principal_optional(req, header_token=None)
    assert prin is not None
    assert prin.id == f"tailscale:{_LOGIN}"


def test_resolve_principal_optional_none_without_tailnet_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    req = _mock_request(transport_peer="127.0.0.1", client_host=_TAILNET_CLIENT_IP, headers={})
    if hasattr(req, "session"):
        del req.session
    assert _identity._resolve_principal_optional(req, header_token=None) is None


# ---------------------------------------------------------------------------
# resolve_perimeter_principal wiring
# ---------------------------------------------------------------------------


def test_resolve_perimeter_principal_admits_tailnet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    _mock_whois_matching(monkeypatch, login=_LOGIN)
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=_TAILNET_CLIENT_IP,
        headers={"Tailscale-User-Login": _LOGIN},
    )
    if hasattr(req, "session"):
        del req.session

    prin = _identity.resolve_perimeter_principal(req)
    assert prin is not None
    assert prin.id == f"tailscale:{_LOGIN}"


# ---------------------------------------------------------------------------
# require_scope checker wiring
# ---------------------------------------------------------------------------


async def test_require_scope_checker_admits_tailnet_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    _mock_whois_matching(monkeypatch, login=_LOGIN, host=_LOOPBACK_PEER[0], port=_LOOPBACK_PEER[1])

    app = FastAPI()
    _scope_dep = Depends(_identity.require_scope("staff.dispatch"))

    @app.get("/scoped")
    def scoped(principal: Principal = _scope_dep):
        return {"id": principal.id}

    wrapped = build_asgi_app(app)

    async with AsyncClient(
        transport=ASGITransport(app=wrapped, client=_LOOPBACK_PEER),
        base_url="http://testserver",
    ) as ac:
        resp = await ac.get(
            "/scoped",
            headers={
                "X-Forwarded-For": _TAILNET_CLIENT_IP,
                "Tailscale-User-Login": _LOGIN,
            },
        )

    assert resp.status_code == 200
    assert resp.json()["id"] == f"tailscale:{_LOGIN}"


# ---------------------------------------------------------------------------
# Middleware ordering: raw peer recorded before proxy-header rewriting
# ---------------------------------------------------------------------------


async def test_middleware_records_transport_peer_before_xff_rewrite() -> None:
    """TransportPeerMiddleware must see the real socket peer, and
    ProxyHeadersMiddleware must still rewrite scope["client"] for XFF from a
    trusted (loopback) proxy — matching current behaviour exactly."""
    captured: dict[str, object] = {}

    async def inner_app(scope, receive, send):
        captured["client"] = scope.get("client")
        captured["transport_peer"] = scope.get("rd.transport_peer")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    wrapped = build_asgi_app(inner_app)

    async with AsyncClient(
        transport=ASGITransport(app=wrapped, client=_LOOPBACK_PEER),
        base_url="http://testserver",
    ) as ac:
        resp = await ac.get("/", headers={"X-Forwarded-For": _TAILNET_CLIENT_IP})

    assert resp.status_code == 200
    # Resolved client is rewritten to the forwarded (phone) address...
    assert captured["client"][0] == _TAILNET_CLIENT_IP
    # ...but the raw transport peer recorded by our middleware is still loopback.
    assert captured["transport_peer"] == "127.0.0.1"


async def test_middleware_records_transport_peer_for_direct_tailnet_caller() -> None:
    """A direct tailnet caller (no proxy in front) has raw peer == resolved
    client, and neither is loopback."""
    captured: dict[str, object] = {}

    async def inner_app(scope, receive, send):
        captured["client"] = scope.get("client")
        captured["transport_peer"] = scope.get("rd.transport_peer")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    wrapped = build_asgi_app(inner_app)

    async with AsyncClient(
        transport=ASGITransport(app=wrapped, client=(_TAILNET_CLIENT_IP, 4444)),
        base_url="http://testserver",
    ) as ac:
        # Even if a forged header is present, ProxyHeadersMiddleware only
        # trusts loopback proxies, so scope["client"] stays the raw peer.
        resp = await ac.get("/", headers={"X-Forwarded-For": "100.64.254.99"})

    assert resp.status_code == 200
    assert captured["transport_peer"] == _TAILNET_CLIENT_IP
    assert captured["client"][0] == _TAILNET_CLIENT_IP


async def test_transport_peer_middleware_sets_none_when_no_client() -> None:
    """A scope with no client (e.g. lifespan) must not raise."""
    captured: dict[str, object] = {}

    async def inner_app(scope, receive, send):
        captured["transport_peer"] = scope.get("rd.transport_peer")

    mw = TransportPeerMiddleware(inner_app)
    await mw({"type": "http", "client": None}, None, None)
    assert captured["transport_peer"] is None


# ---------------------------------------------------------------------------
# #1770 / #1786: every tailnet and loopback sign-in may approve
# ---------------------------------------------------------------------------

_SELF_TAILNET_IP = "100.64.254.103"


def _signed_in(monkeypatch: pytest.MonkeyPatch, client_host: str, self_ips: str | None) -> Principal:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    if self_ips is not None:
        monkeypatch.setenv("DASHBOARD_TAILSCALE_SELF_IPS", self_ips)
    _mock_whois_matching(monkeypatch, login=_LOGIN)
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=client_host,
        headers={"Tailscale-User-Login": _LOGIN},
    )
    prin = tailnet_principal(req)
    assert prin is not None
    return prin


def test_tailnet_signin_from_other_device_can_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    prin = _signed_in(monkeypatch, _TAILNET_CLIENT_IP, f"{_SELF_TAILNET_IP}, fd7a:115c:a1e0::1")
    assert "tailnet-approver" in prin.roles
    assert _identity.principal_has_scope(prin, "staff.approve")
    assert not _identity.principal_has_scope(prin, "staff.admin")


def test_tailnet_signin_from_host_itself_can_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1786: the owner approves from anywhere, including a browser on this host."""
    prin = _signed_in(monkeypatch, _SELF_TAILNET_IP, _SELF_TAILNET_IP)
    assert prin.roles == ["loopback"]
    assert _identity.principal_has_scope(prin, "staff.approve")


def test_tailnet_signin_without_self_ips_can_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    prin = _signed_in(monkeypatch, _TAILNET_CLIENT_IP, None)
    assert prin.roles == ["loopback"]
    assert _identity.principal_has_scope(prin, "staff.approve")


def test_loopback_principal_can_approve_but_is_not_admin() -> None:
    """#1786: Desk approves; approval still does not grant admin."""
    prin = _identity._loopback_principal()
    assert _identity.principal_has_scope(prin, "staff.approve")
    assert not _identity.principal_has_scope(prin, "staff.admin")


def test_direct_tailnet_connection_without_serve_headers_when_direct_allow_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_DIRECT_ALLOW", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_OPERATOR_WRITES", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", "operator@github,user@example.com")
    _mock_whois_matching(monkeypatch, login="operator@github", host=_TAILNET_CLIENT_IP)
    req = _mock_request(
        transport_peer=_TAILNET_CLIENT_IP,
        client_host=_TAILNET_CLIENT_IP,
        headers={},
    )
    prin = tailnet_principal(req)
    assert prin is not None
    assert prin.id == "tailscale:operator@github"
    assert prin.name == "operator@github"
    assert "operator" in prin.roles
    assert "admin" in prin.roles
    assert _identity.principal_has_scope(prin, "staff.write")
    assert _identity.principal_has_scope(prin, "staff.admin")


# ---------------------------------------------------------------------------
# #3011: headers alone are not proof of identity — tailscaled's own WhoIs
# record for this exact peer/port must corroborate the claimed login.
# ---------------------------------------------------------------------------


def test_tailnet_principal_refused_when_whois_has_no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local process that forges the serve headers over its own loopback
    connection is refused: tailscaled never proxied that connection, so it
    has no WhoIs record for this peer/port (the pre-#3011 vulnerability)."""
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", _LOGIN)
    monkeypatch.setattr(_tailnet_identity, "_tailscaled_whois", lambda host, port: None)
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=_TAILNET_CLIENT_IP,
        headers={"Tailscale-User-Login": _LOGIN, "Tailscale-User-Name": "Alice"},
    )
    assert tailnet_principal(req) is None


def test_tailnet_principal_refused_when_whois_names_a_different_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tailscaled proxied *some* connection on this peer/port, but for a
    different login than the header claims — refused, not trusted."""
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", f"{_LOGIN},mallory@example.com")
    _mock_whois_matching(monkeypatch, login="mallory@example.com")
    req = _mock_request(
        transport_peer="127.0.0.1",
        client_host=_TAILNET_CLIENT_IP,
        headers={"Tailscale-User-Login": _LOGIN},
    )
    assert tailnet_principal(req) is None


def test_tailnet_principal_direct_allow_refused_without_whois_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """DASHBOARD_TAILSCALE_DIRECT_ALLOW must not blindly admit the first
    configured login either: it still needs tailscaled's own confirmation."""
    monkeypatch.setenv("DASHBOARD_TAILSCALE_AUTH", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_DIRECT_ALLOW", "1")
    monkeypatch.setenv("DASHBOARD_TAILSCALE_LOGINS", "operator@github,user@example.com")
    monkeypatch.setattr(_tailnet_identity, "_tailscaled_whois", lambda host, port: None)
    req = _mock_request(
        transport_peer=_TAILNET_CLIENT_IP,
        client_host=_TAILNET_CLIENT_IP,
        headers={},
    )
    assert tailnet_principal(req) is None


def test_tailscaled_whois_fails_closed_on_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """No tailscaled socket reachable (e.g. not installed/running) -> None,
    never an exception that could be mishandled by a caller."""

    def _raise_connect(self: object) -> None:
        raise OSError("no such file or directory")

    monkeypatch.setattr(_tailnet_identity._UnixSocketHTTPConnection, "connect", _raise_connect)
    assert _tailnet_identity._tailscaled_whois("127.0.0.1", 54321) is None


def test_tailscaled_whois_none_without_host_or_port() -> None:
    assert _tailnet_identity._tailscaled_whois(None, 54321) is None
    assert _tailnet_identity._tailscaled_whois("127.0.0.1", None) is None


def test_tailscaled_whois_parses_verified_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """The happy path: a 200 response with a UserProfile yields a WhoIsIdentity."""

    class _FakeResponse:
        status = 200

        def read(self) -> bytes:
            return json.dumps({"UserProfile": {"LoginName": _LOGIN, "DisplayName": "Alice"}}).encode()

    class _FakeConn:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def request(self, *_args: object, **_kwargs: object) -> None:
            pass

        def getresponse(self) -> _FakeResponse:
            return _FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr(_tailnet_identity, "_UnixSocketHTTPConnection", _FakeConn)
    result = _tailnet_identity._tailscaled_whois("127.0.0.1", 54321)
    assert result == WhoIsIdentity(login=_LOGIN, name="Alice")
