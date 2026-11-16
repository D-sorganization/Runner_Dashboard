"""Fleet-control fan-out credentials and upstream-failure status (#4274 F-05).

The hub fans ``POST /api/fleet/control/{action}`` out to every node. That call
must carry the same hub -> node headers as every other intra-fleet call (fleet
bearer token + CSRF sentinel), and a node that refuses or cannot be reached
must not be reported to the operator as HTTP 200.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal
from routers import orchestration
from routers.orchestration import FLEET_CONTROL_CALLER, OrchestrationDeps, orchestration_deps

_FLEET_TOKEN = "f05-fleet-token-fixture"  # noqa: S105  # pragma: allowlist secret
_NODE_URL = "http://node-a.test:8321"


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _RecordingClient:
    """Stand-in for ``httpx.AsyncClient`` that records the outgoing POST."""

    calls: list[dict[str, Any]] = []
    response = _FakeResponse(200, {"machine": "node-a", "results": []})

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _RecordingClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        type(self).calls.append({"url": url, **kwargs})
        return type(self).response


@pytest.fixture
def recording_client(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingClient]:
    import server  # noqa: PLC0415

    _RecordingClient.calls = []
    _RecordingClient.response = _FakeResponse(200, {"machine": "node-a", "results": []})
    monkeypatch.setattr(server.httpx, "AsyncClient", _RecordingClient)
    return _RecordingClient


def test_fanout_sends_fleet_bearer_and_csrf_sentinel(
    monkeypatch: pytest.MonkeyPatch, recording_client: type[_RecordingClient]
) -> None:
    import server  # noqa: PLC0415

    monkeypatch.setenv("HUB_FLEET_TOKEN", _FLEET_TOKEN)

    result = asyncio.run(server._remote_fleet_control("node-a", _NODE_URL, "all-up"))

    assert result["success"] is True
    (call,) = recording_client.calls
    assert call["url"] == f"{_NODE_URL}/api/fleet/control/all-up?local=1"
    headers = call.get("headers") or {}
    assert headers.get("X-Requested-With") == "XMLHttpRequest"
    assert headers.get("Authorization") == f"Bearer {_FLEET_TOKEN}"


def test_fanout_sends_csrf_sentinel_without_token(
    monkeypatch: pytest.MonkeyPatch, recording_client: type[_RecordingClient]
) -> None:
    import server  # noqa: PLC0415

    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)

    asyncio.run(server._remote_fleet_control("node-a", _NODE_URL, "all-up"))

    headers = recording_client.calls[0].get("headers") or {}
    assert headers.get("X-Requested-With") == "XMLHttpRequest"
    assert "Authorization" not in headers


def test_fanout_node_refusal_never_echoes_the_token(
    monkeypatch: pytest.MonkeyPatch, recording_client: type[_RecordingClient]
) -> None:
    import server  # noqa: PLC0415

    monkeypatch.setenv("HUB_FLEET_TOKEN", _FLEET_TOKEN)
    recording_client.response = _FakeResponse(401, {"error": "Authentication required"})

    result = asyncio.run(server._remote_fleet_control("node-a", _NODE_URL, "all-up"))

    assert result["success"] is False
    assert result["status_code"] == 401
    assert _FLEET_TOKEN not in str(result)


def _hub_client(monkeypatch: pytest.MonkeyPatch, remote_result: dict[str, Any]) -> TestClient:
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    monkeypatch.setattr(orchestration, "MACHINE_ROLE", "hub")
    monkeypatch.setattr(orchestration, "FLEET_NODES", {"node-a": _NODE_URL})
    deps = OrchestrationDeps(
        fleet_control_local=AsyncMock(return_value={"machine": "hub", "action": "all-up", "results": []}),
        remote_fleet_control=AsyncMock(return_value=remote_result),
        get_fleet_nodes_impl=AsyncMock(return_value={"nodes": []}),
        run_cmd=AsyncMock(return_value=(0, "{}", "")),
    )
    app = FastAPI()
    app.dependency_overrides[orchestration_deps] = lambda: deps
    app.dependency_overrides[FLEET_CONTROL_CALLER] = lambda: Principal(
        id="test-operator", type="user", name="Test Operator", scopes={"fleet.control"}
    )
    app.include_router(orchestration.router)
    return TestClient(app, raise_server_exceptions=True)


def test_upstream_node_failure_is_not_reported_as_200(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = {"machine": "node-a", "url": _NODE_URL, "success": False, "status_code": 403, "error": "CSRF"}
    client = _hub_client(monkeypatch, failed)

    resp = client.post("/api/fleet/control/all-up")

    assert resp.status_code == 502
    data = resp.json()
    assert data["error"] == "upstream_error"
    assert "node-a" in data["detail"]
    by_machine = {node["machine"]: node for node in data["nodes"]}
    assert by_machine["hub"]["success"] is True
    assert by_machine["node-a"]["success"] is False
    assert by_machine["node-a"]["status_code"] == 403


def test_all_nodes_succeeding_still_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = {"machine": "node-a", "url": _NODE_URL, "success": True, "result": {"results": []}}
    client = _hub_client(monkeypatch, ok)

    resp = client.post("/api/fleet/control/all-up")

    assert resp.status_code == 200
    data = resp.json()
    assert "error" not in data
    assert [node["success"] for node in data["nodes"]] == [True, True]


def test_node_with_nothing_to_do_is_not_an_upstream_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    idle = {"machine": "node-a", "url": _NODE_URL, "success": False, "status_code": 400, "error": "No idle runners"}
    client = _hub_client(monkeypatch, idle)

    resp = client.post("/api/fleet/control/down")

    assert resp.status_code == 200
    by_machine = {node["machine"]: node for node in resp.json()["nodes"]}
    assert by_machine["node-a"]["success"] is False, "the per-node outcome is still reported"


def _real_auth_client(monkeypatch: pytest.MonkeyPatch, role: str) -> tuple[TestClient, OrchestrationDeps]:
    """The route behind the real auth dependencies: only the fleet token is configured."""
    monkeypatch.setenv("HUB_FLEET_TOKEN", _FLEET_TOKEN)
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setattr(orchestration, "MACHINE_ROLE", role)
    monkeypatch.setattr(orchestration, "FLEET_NODES", {"node-a": _NODE_URL} if role == "hub" else {})
    deps = OrchestrationDeps(
        fleet_control_local=AsyncMock(return_value={"machine": "node-a", "action": "all-up", "results": []}),
        remote_fleet_control=AsyncMock(return_value={"machine": "node-a", "url": _NODE_URL, "success": True}),
        get_fleet_nodes_impl=AsyncMock(return_value={"nodes": []}),
        run_cmd=AsyncMock(return_value=(0, "{}", "")),
    )
    app = FastAPI()
    app.dependency_overrides[orchestration_deps] = lambda: deps
    app.include_router(orchestration.router)
    return TestClient(app, raise_server_exceptions=True), deps


_FANOUT_HEADERS = {"Authorization": f"Bearer {_FLEET_TOKEN}", "X-Requested-With": "XMLHttpRequest"}


def test_node_accepts_the_hub_fleet_token_for_a_local_only_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact request ``_remote_fleet_control`` sends must pass the node's real scope check."""
    client, deps = _real_auth_client(monkeypatch, "node")

    resp = client.post("/api/fleet/control/all-up?local=1", headers=_FANOUT_HEADERS)

    assert resp.status_code == 200, resp.text
    deps.fleet_control_local.assert_awaited_once_with("all-up")


def test_fleet_token_cannot_request_a_fleet_wide_action(monkeypatch: pytest.MonkeyPatch) -> None:
    client, deps = _real_auth_client(monkeypatch, "hub")

    resp = client.post("/api/fleet/control/all-down", headers=_FANOUT_HEADERS)

    assert resp.status_code == 403
    assert resp.json()["detail"]["required_scope"] == "fleet.control"
    deps.fleet_control_local.assert_not_awaited()
    deps.remote_fleet_control.assert_not_awaited()


def test_local_only_call_on_a_hub_does_not_fan_out(monkeypatch: pytest.MonkeyPatch) -> None:
    client, deps = _real_auth_client(monkeypatch, "hub")

    resp = client.post("/api/fleet/control/all-up?local=1", headers=_FANOUT_HEADERS)

    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "local"
    deps.remote_fleet_control.assert_not_awaited()


def test_wrong_bearer_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, deps = _real_auth_client(monkeypatch, "node")

    resp = client.post(
        "/api/fleet/control/all-up?local=1",
        headers={"Authorization": "Bearer not-the-fleet-token", "X-Requested-With": "XMLHttpRequest"},
    )

    assert resp.status_code in (401, 403)
    deps.fleet_control_local.assert_not_awaited()
