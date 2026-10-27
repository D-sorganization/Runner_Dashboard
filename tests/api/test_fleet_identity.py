"""Tests for local node identity resolution and GET /api/fleet/identity (issue #1291)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import machine_registry as mr  # noqa: E402
from routers.orchestration_node_routes import router as orchestration_router  # noqa: E402

_SAMPLE_REGISTRY = {
    "version": 1,
    "machines": [
        {
            "name": "node-a",
            "aliases": ["node-a", "control-tower"],
            "display_name": "node-a",
            "role": "hub",
            "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
            "runner_pools": [
                {
                    "name": "node-a-Runner",
                    "aliases": ["node-a-runner", "node-a-runner"],
                    "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
                    "role": "runner_pool",
                }
            ],
        },
        {
            "name": "hub-a",
            "aliases": ["hub-a", "desktop", "desk-computer"],
            "display_name": "hub-a",
            "role": "node",
            "dashboard_url": "http://hub-a.fleet-example.ts.net:8321",
        },
        {
            "name": "node-b",
            "aliases": ["og-laptop", "node-b"],
            "display_name": "node-b",
            "role": "node",
            "dashboard_url": "http://node-b.fleet-example.ts.net:8321",
        },
    ],
}


# ---------------------------------------------------------------------------
# Unit tests: resolve_local_identity
# ---------------------------------------------------------------------------


def test_resolve_local_identity_exact_match() -> None:
    res = mr.resolve_local_identity("hub-a", "node", _SAMPLE_REGISTRY)
    assert res["name"] == "hub-a"
    assert res["role"] == "node"
    assert res["registry_match"] == "hub-a"
    assert res["matched_alias"] == "huba"


def test_resolve_local_identity_alias_match() -> None:
    res = mr.resolve_local_identity("desktop", "node", _SAMPLE_REGISTRY)
    assert res["name"] == "desktop"
    assert res["role"] == "node"
    assert res["registry_match"] == "hub-a"
    assert res["matched_alias"] == "desktop"


def test_resolve_local_identity_runner_pool_match() -> None:
    res = mr.resolve_local_identity("node-a-Runner", "node", _SAMPLE_REGISTRY)
    assert res["name"] == "node-a-Runner"
    assert res["registry_match"] == "node-a-Runner"
    assert res["matched_alias"] == "nodearunner"


def test_resolve_local_identity_never_matches_by_role() -> None:
    """A node with role 'hub' must never borrow node-a's identity if its name doesn't match."""
    res = mr.resolve_local_identity("hub-a", "hub", _SAMPLE_REGISTRY)
    assert res["name"] == "hub-a"
    assert res["registry_match"] == "hub-a"
    assert res["registry_match"] != "node-a"


def test_resolve_local_identity_unregistered_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        res = mr.resolve_local_identity("MysteryLaptop", "node", _SAMPLE_REGISTRY)
    assert res["name"] == "MysteryLaptop"
    assert res["role"] == "node"
    assert res["registry_match"] == "unregistered"
    assert res["matched_alias"] is None
    assert any("MysteryLaptop" in record.message and "unregistered" in record.message for record in caplog.records)


def test_resolve_local_identity_env_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISPLAY_NAME", "node-b")
    monkeypatch.setenv("MACHINE_ROLE", "node")
    res = mr.resolve_local_identity(None, None, _SAMPLE_REGISTRY)
    assert res["name"] == "node-b"
    assert res["registry_match"] == "node-b"


# ---------------------------------------------------------------------------
# API tests: GET /api/fleet/identity
# ---------------------------------------------------------------------------


def test_get_fleet_identity_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.include_router(orchestration_router)

    monkeypatch.setenv("DISPLAY_NAME", "hub-a")
    monkeypatch.setenv("MACHINE_ROLE", "node")
    monkeypatch.setattr(mr, "load_machine_registry", lambda: _SAMPLE_REGISTRY)

    client = TestClient(app)
    resp = client.get("/api/fleet/identity")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "hub-a"
    assert data["role"] == "node"
    assert data["registry_match"] == "hub-a"
    assert data["matched_alias"] == "huba"
    assert data["unregistered"] is False
    assert "hub-a" in data["aliases"]


def test_get_fleet_identity_unregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.include_router(orchestration_router)

    monkeypatch.setenv("DISPLAY_NAME", "UnknownNode")
    monkeypatch.setenv("MACHINE_ROLE", "node")
    monkeypatch.setattr(mr, "load_machine_registry", lambda: _SAMPLE_REGISTRY)

    client = TestClient(app)
    resp = client.get("/api/fleet/identity")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "UnknownNode"
    assert data["role"] == "node"
    assert data["registry_match"] == "unregistered"
    assert data["unregistered"] is True
    assert data["aliases"] == []
    assert data["runner_labels"] == []
    assert data["specs"] == {}


def test_get_fleet_nodes_does_not_proxy_to_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spoke nodes must serve /api/fleet/nodes locally so only themselves are marked is_local."""
    import proxy_utils
    from routers.orchestration_node_routes import OrchestrationNodeDeps, orchestration_node_deps

    app = FastAPI()
    app.include_router(orchestration_router)

    local_payload = {
        "nodes": [
            {"name": "hub-a", "url": "http://localhost:8321", "is_local": True, "online": True},
            {"name": "node-a", "url": "http://node-a:8321", "is_local": False, "online": True},
        ],
        "count": 2,
        "online_count": 2,
    }

    deps = OrchestrationNodeDeps(
        get_fleet_nodes_impl=AsyncMock(return_value=local_payload),
        get_system_metrics_snapshot=AsyncMock(),
    )
    app.dependency_overrides[orchestration_node_deps] = lambda: deps

    # Force should_proxy_fleet_to_hub to True to verify /api/fleet/nodes does NOT proxy
    monkeypatch.setattr(proxy_utils, "should_proxy_fleet_to_hub", lambda _req: True)
    proxy_mock = AsyncMock(return_value={"nodes": [{"name": "node-a", "is_local": True}]})
    monkeypatch.setattr(proxy_utils, "proxy_to_hub", proxy_mock)

    client = TestClient(app)
    resp = client.get("/api/fleet/nodes")
    assert resp.status_code == 200
    data = resp.json()

    # Must NOT have proxied to hub
    assert proxy_mock.call_count == 0
    # Must have served local payload where hub-a is local
    assert data["nodes"][0]["name"] == "hub-a"
    assert data["nodes"][0]["is_local"] is True


def test_merge_registry_suppresses_runner_pool_offline_when_parent_is_live() -> None:
    live = [{"name": "node-a", "url": "http://node-a.fleet-example.ts.net:8321", "online": True, "is_local": True}]
    merged = mr.merge_registry_with_live_nodes(live, _SAMPLE_REGISTRY)
    names = [n["name"] for n in merged]
    # node-a must be present
    assert "node-a" in names
    # node-a-Runner should NOT be appended as an offline duplicate
    assert "node-a-Runner" not in names


def test_merge_registry_deduplicates_local_and_remote() -> None:
    live = [
        {"name": "hub-a", "url": "http://localhost:8321", "is_local": True, "online": True},
        {
            "name": "hub-a",
            "url": "http://hub-a.fleet-example.ts.net:8321",
            "is_local": False,
            "online": True,
        },
    ]
    merged = mr.merge_registry_with_live_nodes(live, _SAMPLE_REGISTRY)
    desk_entries = [n for n in merged if n["name"] == "hub-a"]
    # Exactly one entry for hub-a, and it must be marked is_local: True
    assert len(desk_entries) == 1
    assert desk_entries[0]["is_local"] is True
