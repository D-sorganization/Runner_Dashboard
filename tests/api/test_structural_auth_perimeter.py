"""Structural auth-perimeter tests (issue #924).

Before #924, authentication was opt-in per route: a handler that forgot to
declare ``Depends(require_principal)`` shipped wide open, and the
``_AUTH_EXEMPT_PATHS`` list was consumed only by the CSRF check, not by any auth
enforcer. The agent-launcher (#920), web-vitals / runner-audit / runner
diagnostics (#928) holes all stemmed from this missing structural guarantee.

The fix is a fail-closed perimeter middleware (``auth_perimeter_check``) plus
this test, which walks ``app.routes`` and asserts that **every** mutating
``/api/*`` route is one of:

  1. protected by a ``require_*`` auth dependency, or
  2. explicitly listed in ``_AUTH_EXEMPT_PATHS`` (health / auth handshake /
     signed webhook / logout), or
  3. covered by a documented alternate-auth prefix (``_ALT_AUTH_EXEMPT_PREFIXES``:
     HMAC dispatch envelopes, the feature-flagged Conductor orchestrator).

A new route that is none of these fails the build — the perimeter can no longer
silently regress to opt-in.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from starlette.routing import Route

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import server  # noqa: E402
from middleware import (  # noqa: E402
    _ALT_AUTH_EXEMPT_PREFIXES,
    _AUTH_EXEMPT_PATHS,
    is_auth_exempt,
)

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_AUTH_DEP_NAMES = {"require_principal", "require_fleet_peer", "require_scope", "checker"}


def _dependency_callable_names(route: Route) -> set[str]:
    """Collect the __name__ of every callable in a route's dependency tree."""
    names: set[str] = set()

    def _walk(dep) -> None:
        if dep is None:
            return
        call = getattr(dep, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
        for sub in getattr(dep, "dependencies", []):
            _walk(sub)

    _walk(getattr(route, "dependant", None))
    return names


def _route_is_auth_protected(route: Route) -> bool:
    return bool(_dependency_callable_names(route) & _AUTH_DEP_NAMES)


def _mutating_api_routes() -> list[tuple[str, str, Route]]:
    rows: list[tuple[str, str, Route]] = []
    for route in server.app.routes:
        if not isinstance(route, Route) or not route.methods:
            continue
        if not route.path.startswith("/api/"):
            continue
        methods = route.methods & _MUTATING_METHODS
        if not methods:
            continue
        rows.append((sorted(methods)[0], route.path, route))
    return rows


def test_every_mutating_api_route_is_protected_or_exempt() -> None:
    """No mutating /api/* route may be unauthenticated unless explicitly exempt.

    This is the #924 acceptance criterion: the build fails if any POST/PUT/PATCH/
    DELETE /api/* route is neither auth-protected nor on a documented exempt list.
    """
    unprotected: list[str] = []
    for method, path, route in _mutating_api_routes():
        if is_auth_exempt(path):
            continue
        if _route_is_auth_protected(route):
            continue
        unprotected.append(f"{method} {path}")

    assert not unprotected, (
        "Unauthenticated mutating /api/* routes (the #924 regression class) — each "
        "must carry a require_* dependency or be added to _AUTH_EXEMPT_PATHS / "
        f"_ALT_AUTH_EXEMPT_PREFIXES with justification: {sorted(set(unprotected))}"
    )


def test_alt_auth_prefixes_actually_cover_routes() -> None:
    """Guard the alt-auth allowlist: every prefix must match a real route, so a
    stale entry (e.g. after a route is removed) surfaces instead of silently
    widening the perimeter."""
    all_paths = {route.path for route in server.app.routes if isinstance(route, Route)}
    for prefix in _ALT_AUTH_EXEMPT_PREFIXES:
        assert any(p.startswith(prefix) for p in all_paths), (
            f"_ALT_AUTH_EXEMPT_PREFIXES entry {prefix!r} matches no route; prune it."
        )


def test_exempt_paths_are_minimal_and_explicit() -> None:
    """The exempt set must stay an explicit, reviewed allowlist (no wildcards)."""
    for path in _AUTH_EXEMPT_PATHS:
        assert isinstance(path, str) and path.startswith("/"), path
        assert "*" not in path, f"exempt paths must be exact, not globbed: {path}"


# ─── HTTP-level: previously-open routes now 401 unauthenticated ───────────────
#
# TestClient connects from 127.0.0.1. The loopback admin bypass is gated on
# DASHBOARD_LOOPBACK_AUTH=1 (issue #315); with it unset, the perimeter must
# reject. We assert the gate fires for routes that were unauthenticated before
# #924/#928 (web-vitals, runner-routing-audit refresh, runner diagnostics).

_PREVIOUSLY_OPEN_ROUTES = [
    ("post", "/api/metrics/web-vitals", {"route": "/", "metrics": []}),
    ("post", "/api/runner-routing-audit/refresh", None),
    ("post", "/api/runners/123/diagnostics", None),
    ("post", "/api/linear/sync/poll", None),
    ("post", "/api/autoscaler/pools/cpu/config", {"min_online": 0}),
]


@pytest.fixture
def _no_loopback_bypass(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DASHBOARD_LOOPBACK_AUTH", raising=False)
    server.app.dependency_overrides.clear()
    yield
    server.app.dependency_overrides.clear()


@pytest.mark.parametrize(("method", "url", "body"), _PREVIOUSLY_OPEN_ROUTES)
def test_previously_open_routes_now_require_auth(_no_loopback_bypass, method: str, url: str, body) -> None:
    from fastapi.testclient import TestClient

    client = TestClient(server.app, raise_server_exceptions=False)
    headers = {"X-Requested-With": "XMLHttpRequest"}
    resp = (
        getattr(client, method)(url, json=body, headers=headers)
        if body is not None
        else getattr(client, method)(url, headers=headers)
    )
    assert resp.status_code == 401, (
        f"{method.upper()} {url} must be rejected by the structural perimeter, got {resp.status_code}"
    )


def test_exempt_route_still_reachable_unauthenticated(_no_loopback_bypass) -> None:
    """An exempt route (health) must NOT be blocked by the perimeter."""
    from fastapi.testclient import TestClient

    client = TestClient(server.app, raise_server_exceptions=False)
    resp = client.get("/api/health")
    assert resp.status_code != 401


def test_dependency_override_defers_to_route(_no_loopback_bypass) -> None:
    """When require_principal is overridden (test injection), the perimeter must
    defer to the route dependency rather than re-checking credentials."""
    from fastapi.testclient import TestClient
    from identity import Principal, require_principal

    server.app.dependency_overrides[require_principal] = lambda: Principal(
        id="test-admin", type="bot", name="Admin", roles=["admin"]
    )
    try:
        client = TestClient(server.app, raise_server_exceptions=False)
        resp = client.post(
            "/api/runner-routing-audit/refresh",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        # Perimeter deferred; the route ran (any non-401 proves the gate yielded).
        assert resp.status_code != 401
    finally:
        server.app.dependency_overrides.clear()


def test_fleet_read_endpoints_are_exempt() -> None:
    """Hub→node fleet telemetry reads must not be force-401'd by the perimeter.

    The hub fans out to `{node}/api/system` (and peer pools to
    `/api/fleet/status`) with no operator principal; these are tailnet-scoped
    read-only metrics. Regression guard for the whole fleet showing offline-401
    after every node moved to >=4.9 code.
    """
    assert is_auth_exempt("/api/system")
    assert is_auth_exempt("/api/fleet/status")


def test_fleet_status_keeps_its_own_fleet_peer_dependency() -> None:
    """Exempting `/api/fleet/status` from the structural perimeter must not strip
    its dedicated `require_fleet_peer` auth — it stays governed by the fleet
    model, just not by the operator-principal perimeter."""
    routes = [r for r in server.app.routes if getattr(r, "path", None) == "/api/fleet/status"]
    assert routes, "/api/fleet/status route not found"
    names: set[str] = set()
    for r in routes:
        names |= _dependency_callable_names(r)
    assert "require_fleet_peer" in names
