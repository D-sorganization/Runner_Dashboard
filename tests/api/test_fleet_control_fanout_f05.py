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
from identity import Principal, require_scope
from routers import orchestration
from routers.orchestration import OrchestrationDeps, orchestration_deps

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
    app.dependency_overrides[require_scope("fleet.control")] = lambda: Principal(
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
