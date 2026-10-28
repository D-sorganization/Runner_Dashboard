"""Sunset policy for legacy HTTP 410 routes (30-day removal window)."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from retired_route_sunset import RETIRED_ROUTE_HTTP_SUNSET, RETIRED_ROUTE_REMOVAL_AFTER
from server import app

_RETIRED_OPENAPI_PATHS = (
    "/api/agents/quick-dispatch",
    "/api/agent-remediation/dispatch-jules",
    "/api/assistant/chat",
)


def test_retired_410_routes_removal_deadline_not_passed() -> None:
    """Fails after the sunset date: delete 410 handlers and scrub OpenAPI snapshots."""
    assert date.today() <= RETIRED_ROUTE_REMOVAL_AFTER, (
        f"Sunset {RETIRED_ROUTE_REMOVAL_AFTER} passed: remove remediation_retired routes, "
        "assistant chat 410 handler, and retired OpenAPI path entries"
    )


def test_retired_410_paths_excluded_from_live_openapi() -> None:
    """Retired endpoints stay callable but must not appear in generated OpenAPI."""
    schema = TestClient(app, raise_server_exceptions=False).get("/openapi.json").json()
    paths = schema.get("paths", {})
    for path in _RETIRED_OPENAPI_PATHS:
        assert path not in paths, f"{path} should be include_in_schema=False"


def test_retired_dispatch_routes_use_shared_sunset_header() -> None:
    assert RETIRED_ROUTE_HTTP_SUNSET == "Sun, 23 Nov 2026 00:00:00 GMT"
