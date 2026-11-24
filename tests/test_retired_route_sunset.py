"""Sunset policy for legacy HTTP 410 routes (removed after 2026-11-23)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from server import app

_RETIRED_OPENAPI_PATHS = (
    "/api/agents/quick-dispatch",
    "/api/agent-remediation/dispatch-jules",
    "/api/assistant/chat",
)


def test_retired_410_routes_unmounted_after_sunset(make_authed_client: Any, operator_principal: Any) -> None:
    """Legacy 410 stubs are removed once the sunset window closes."""
    client = make_authed_client(operator_principal)
    for path in _RETIRED_OPENAPI_PATHS:
        resp = client.post(path, headers={"X-Requested-With": "XMLHttpRequest"}, json={})
        assert resp.status_code not in (200, 410), path
        assert resp.status_code in (404, 405), path


def test_retired_410_paths_excluded_from_live_openapi() -> None:
    """Retired endpoints must not appear in generated OpenAPI."""
    schema = TestClient(app, raise_server_exceptions=False).get("/openapi.json").json()
    paths = schema.get("paths", {})
    for path in _RETIRED_OPENAPI_PATHS:
        assert path not in paths, f"{path} should be absent after sunset removal"
