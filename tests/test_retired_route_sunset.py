"""Legacy HTTP 410 routes were removed after the 2026-11-23 sunset (SC-G5-6 / SC-D11)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from server import app

_RETIRED_PATHS = (
    "/api/agents/quick-dispatch",
    "/api/agent-remediation/dispatch-jules",
    "/api/assistant/chat",
)


def test_retired_paths_are_unmounted(make_authed_client, operator_principal) -> None:
    client = make_authed_client(operator_principal)
    for path in _RETIRED_PATHS:
        resp = client.post(path, headers={"X-Requested-With": "XMLHttpRequest"}, json={})
        assert resp.status_code in (404, 405), (path, resp.status_code, resp.text[:200])


def test_retired_paths_excluded_from_live_openapi() -> None:
    schema = TestClient(app, raise_server_exceptions=False).get("/openapi.json").json()
    paths = schema.get("paths", {})
    for path in _RETIRED_PATHS:
        assert path not in paths, f"{path} should not appear in OpenAPI"
