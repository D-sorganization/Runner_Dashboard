"""Route-table invariants (issue #940).

Two routers must never register the same (method, path) pair. FastAPI resolves
the first match wins, so a duplicate silently shadows the maintained handler.
Before #940, backend/metrics.py registered GET /api/system and
GET /api/fleet/status a second time; because the metrics router was included
before routers.system and routers.fleet, the metrics copies were served and the
maintained fleet.py implementation (which feeds the /api/events poller, #863)
was dead code.

These tests assert:
- No duplicate (method, path) pair exists anywhere in the assembled app.
- GET /api/fleet/status resolves to the routers.fleet implementation (the one
  that records fleet events), not the metrics.py copy.
- backend/metrics.py no longer imports from backend.server (the circular
  lazy-import that only existed to support the duplicate fleet-status handler).
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import server  # noqa: E402
from starlette.routing import Route  # noqa: E402


def _method_path_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for route in server.app.routes:
        if isinstance(route, Route) and route.methods:
            for method in route.methods:
                if method in {"HEAD", "OPTIONS"}:
                    continue
                pairs.append((method, route.path))
    return pairs


# Pre-existing duplicate registrations that are out of scope for #940 (they are
# server.py-god-module / diagnostics.py twins tracked by #941). The diagnostics.py
# (#360) copies are registered LAST and are therefore shadowed dead code; the
# winning copies live in routers.runner_audit and server.py. Untangling them
# means reworking the diagnostics.configure() DI contract, which belongs to #941.
# This allowlist is intentionally exhaustive: the invariant below fails on ANY
# duplicate NOT listed here, so a new shadow (the #940 regression class) is caught
# immediately. When #941 removes these, delete the corresponding entries.
_KNOWN_DUPLICATES_PENDING_941: set[tuple[str, str]] = {
    ("GET", "/api/runner-routing-audit"),
    ("POST", "/api/runner-routing-audit/refresh"),
    # ("POST", "/api/launchers/generate") removed in #941 — the body-identical
    # server.py twin of routers/diagnostics.py's handler was deleted; the route
    # is now registered exactly once.
}


def test_no_duplicate_method_path_pairs() -> None:
    """No (method, path) pair may be registered more than once, except the
    explicitly-tracked #941 god-module twins."""
    counts = Counter(_method_path_pairs())
    duplicates = {pair for pair, n in counts.items() if n > 1}
    unexpected = duplicates - _KNOWN_DUPLICATES_PENDING_941
    assert not unexpected, f"Unexpected duplicate route registrations (the #940 regression class): {sorted(unexpected)}"


def test_metrics_duplicates_are_gone() -> None:
    """The specific #940 duplicates (/api/system, /api/fleet/status from
    metrics.py) must no longer be double-registered."""
    counts = Counter(_method_path_pairs())
    assert counts[("GET", "/api/system")] == 1
    assert counts[("GET", "/api/fleet/status")] == 1


def test_known_941_duplicates_do_not_grow() -> None:
    """Guard the allowlist: every entry must still actually be duplicated, so a
    stale allowlist entry (e.g. once #941 lands) surfaces and gets pruned."""
    counts = Counter(_method_path_pairs())
    stale = {pair for pair in _KNOWN_DUPLICATES_PENDING_941 if counts[pair] <= 1}
    assert not stale, f"Allowlisted pairs are no longer duplicated; prune them: {sorted(stale)}"


def test_fleet_status_resolves_to_fleet_router() -> None:
    """GET /api/fleet/status must resolve to routers.fleet.get_fleet_status,
    the implementation that records fleet events (#863)."""
    import routers.fleet as fleet_router

    target = None
    for route in server.app.routes:
        if isinstance(route, Route) and route.path == "/api/fleet/status" and "GET" in (route.methods or set()):
            target = route
            break

    assert target is not None, "GET /api/fleet/status must be registered"
    assert target.endpoint is fleet_router.get_fleet_status, (
        "GET /api/fleet/status must resolve to routers.fleet.get_fleet_status, "
        f"not {target.endpoint.__module__}.{target.endpoint.__qualname__}"
    )


def test_system_resolves_to_system_router() -> None:
    """GET /api/system must resolve to routers.system, not the metrics copy."""
    import routers.system as system_router

    target = None
    for route in server.app.routes:
        if isinstance(route, Route) and route.path == "/api/system" and "GET" in (route.methods or set()):
            target = route
            break

    assert target is not None, "GET /api/system must be registered"
    assert target.endpoint.__module__ == system_router.__name__, (
        f"GET /api/system must resolve to routers.system, not {target.endpoint.__module__}"
    )


def test_metrics_does_not_import_from_server() -> None:
    """metrics.py must not import from backend.server (no circular dependency)."""
    src = (_BACKEND / "metrics.py").read_text(encoding="utf-8")
    assert "from server import" not in src, "metrics.py must not import from server"
    assert "import server" not in src, "metrics.py must not import server"


def test_metrics_still_serves_pool_pressure() -> None:
    """The unique /api/disk/pool-pressure route in metrics.py must survive."""
    paths = {path for _method, path in _method_path_pairs()}
    assert "/api/disk/pool-pressure" in paths
