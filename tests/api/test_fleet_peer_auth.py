"""Hub-side HUB_FLEET_TOKEN validation tests (issue #922)."""

from __future__ import annotations

import hmac
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

from identity import Principal, require_fleet_peer  # noqa: E402


def _request(*, session: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.session = session if session is not None else {}
    req.client = None
    return req


def test_no_hub_fleet_token_configured_keeps_fleet_reads_tailnet_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)

    assert require_fleet_peer(_request(), header_token=None) == "anonymous:tailnet"


def test_hub_fleet_token_configured_rejects_missing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret

    with pytest.raises(HTTPException) as exc:
        require_fleet_peer(_request(), header_token=None)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Fleet authentication required"


def test_hub_fleet_token_configured_rejects_wrong_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret

    with pytest.raises(HTTPException) as exc:
        require_fleet_peer(_request(), header_token="Bearer wrong-token")

    assert exc.value.status_code == 401


def test_hub_fleet_token_configured_accepts_matching_fleet_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret

    assert require_fleet_peer(_request(), header_token="Bearer the-fleet-token") == "fleet-peer"


def test_hub_fleet_token_uses_constant_time_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret
    calls: list[tuple[str, str]] = []
    original_compare_digest = hmac.compare_digest

    def _compare_digest(left: str, right: str) -> bool:
        calls.append((left, right))
        return original_compare_digest(left, right)

    import identity

    monkeypatch.setattr(identity.hmac, "compare_digest", _compare_digest)

    assert require_fleet_peer(_request(), header_token="Bearer the-fleet-token") == "fleet-peer"
    assert calls == [("the-fleet-token", "the-fleet-token")]


def test_valid_session_principal_satisfies_fleet_peer_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret

    import identity

    principal = Principal(id="alice", type="human", name="Alice", roles=["operator"])
    monkeypatch.setitem(identity.identity_manager.principals, "alice", principal)
    monkeypatch.setattr(identity.sm, "touch_session", lambda session_id: True)

    label = require_fleet_peer(_request(session={"principal_id": "alice", "session_id": "s1"}), header_token=None)

    assert label == "principal:alice"


def test_valid_service_token_principal_satisfies_fleet_peer_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret

    import identity

    principal = Principal(id="bot", type="bot", name="Bot", roles=["bot"])
    monkeypatch.setattr(
        identity.identity_manager,
        "verify_token",
        lambda raw_token: principal if raw_token == "svc-token" else None,
    )

    assert require_fleet_peer(_request(), header_token="Bearer svc-token") == "principal:bot"


def _client_for_router(router) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")  # pragma: allowlist secret
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _route_dependency_calls(router, path: str, method: str) -> list:  # noqa: ANN001, ANN003
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path and method.upper() in route.methods:
            return [dependency.call for dependency in route.dependant.dependencies]
    raise AssertionError(f"Route not found: {method} {path}")


@pytest.mark.parametrize(
    ("module_name", "method", "path"),
    [
        ("routers.fleet", "GET", "/api/fleet/status"),
        ("routers.deployment", "GET", "/api/deployment/state"),
        ("routers.orchestration_node_routes", "GET", "/api/fleet/nodes"),
        ("routers.orchestration_node_routes", "GET", "/api/fleet/hardware"),
        ("routers.queue", "GET", "/api/queue"),
        ("routers.queue", "GET", "/api/queue/status"),
        ("routers.queue", "GET", "/api/queue/stale"),
        ("routers.queue", "POST", "/api/queue/purge-stale"),
        ("routers.repos", "GET", "/api/repos"),
        ("routers.repos_stats", "GET", "/api/stats"),
        ("routers.repos_stats", "GET", "/api/usage"),
        ("routers.runners", "GET", "/api/runners"),
        ("routers.runners", "GET", "/api/runners/matlab"),
        ("routers.runner_diagnostics", "GET", "/api/runners/diagnostics/summary"),
        ("routers.runner_diagnostics", "GET", "/api/runners/fleet/capacity"),
        ("routers.runner_groups", "GET", "/api/runners/groups/{group_label}"),
        ("routers.runs_workflows", "GET", "/api/runs"),
        ("routers.runs_workflows", "GET", "/api/runs/enriched"),
        ("routers.runs_workflows", "GET", "/api/analysis/workflow-machines"),
        ("routers.runs_workflows", "GET", "/api/runs/{repo}"),
        ("routers.runs_workflows", "GET", "/api/scheduled-workflows"),
        ("routers.orchestration", "POST", "/api/fleet/control/{action}"),
    ],
)
def test_hub_proxying_routes_include_fleet_peer_dependency(
    module_name: str,
    method: str,
    path: str,
) -> None:
    module = __import__(module_name, fromlist=["router"])

    assert require_fleet_peer in _route_dependency_calls(module.router, path, method)


def test_fleet_status_route_rejects_anonymous_when_token_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret

    from routers import fleet

    client = _client_for_router(fleet.router)

    assert client.get("/api/fleet/status").status_code == 401


def test_fleet_status_route_accepts_correct_fleet_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret

    from routers import fleet

    async def _metrics() -> dict:
        return {"status": "ok"}

    monkeypatch.setattr(fleet, "MACHINE_ROLE", "hub")
    monkeypatch.setattr(fleet, "FLEET_NODES", {})
    monkeypatch.setattr(fleet, "get_system_metrics_snapshot", _metrics)
    monkeypatch.setattr(fleet, "fetch_org_runners", AsyncMock(return_value={"runners": []}))

    client = _client_for_router(fleet.router)

    response = client.get("/api/fleet/status", headers={"Authorization": "Bearer the-fleet-token"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("module_name", "path"),
    [
        ("routers.fleet", "/api/fleet/status"),
        ("routers.deployment", "/api/deployment/state"),
        ("routers.orchestration_node_routes", "/api/fleet/nodes"),
        ("routers.orchestration_node_routes", "/api/fleet/hardware"),
        ("routers.queue", "/api/queue"),
        ("routers.queue", "/api/queue/status"),
        ("routers.queue", "/api/queue/stale"),
        ("routers.repos", "/api/repos"),
        ("routers.repos_stats", "/api/stats"),
        ("routers.repos_stats", "/api/usage"),
        ("routers.runners", "/api/runners"),
        ("routers.runners", "/api/runners/matlab"),
        ("routers.runner_diagnostics", "/api/runners/diagnostics/summary"),
        ("routers.runner_diagnostics", "/api/runners/fleet/capacity"),
        ("routers.runner_groups", "/api/runners/groups/linux"),
        ("routers.runs_workflows", "/api/runs"),
        ("routers.runs_workflows", "/api/runs/enriched"),
        ("routers.runs_workflows", "/api/analysis/workflow-machines"),
        ("routers.runs_workflows", "/api/runs/Runner_Dashboard"),
        ("routers.runs_workflows", "/api/scheduled-workflows"),
    ],
)
def test_hub_reachable_fleet_read_routes_reject_anonymous_when_token_configured(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    path: str,
) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret
    module = __import__(module_name, fromlist=["router"])
    client = _client_for_router(module.router)

    response = client.get(path)

    assert response.status_code == 401, path
