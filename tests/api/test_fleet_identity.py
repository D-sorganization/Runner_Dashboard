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
            "name": "ControlTower",
            "aliases": ["controltower", "control-tower"],
            "display_name": "ControlTower",
            "role": "hub",
            "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321",
            "runner_pools": [
                {
                    "name": "ControlTower-Runner",
                    "aliases": ["controltower-runner", "control-tower-runner"],
                    "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321",
                    "role": "runner_pool",
                }
            ],
        },
        {
            "name": "DeskComputer",
            "aliases": ["deskcomputer", "desktop", "desk-computer"],
            "display_name": "DeskComputer",
            "role": "node",
            "dashboard_url": "http://deskcomputer.tail2bbcc7.ts.net:8321",
        },
        {
            "name": "OGLaptop",
            "aliases": ["og-laptop", "oglaptop"],
            "display_name": "OGLaptop",
            "role": "node",
            "dashboard_url": "http://oglaptop.tail2bbcc7.ts.net:8321",
        },
    ],
}


# ---------------------------------------------------------------------------
# Unit tests: resolve_local_identity
# ---------------------------------------------------------------------------


def test_resolve_local_identity_exact_match() -> None:
    res = mr.resolve_local_identity("DeskComputer", "node", _SAMPLE_REGISTRY)
    assert res["name"] == "DeskComputer"
    assert res["role"] == "node"
    assert res["registry_match"] == "DeskComputer"
    assert res["matched_alias"] == "deskcomputer"


def test_resolve_local_identity_alias_match() -> None:
    res = mr.resolve_local_identity("desktop", "node", _SAMPLE_REGISTRY)
    assert res["name"] == "desktop"
    assert res["role"] == "node"
    assert res["registry_match"] == "DeskComputer"
    assert res["matched_alias"] == "desktop"


def test_resolve_local_identity_runner_pool_match() -> None:
    res = mr.resolve_local_identity("ControlTower-Runner", "node", _SAMPLE_REGISTRY)
    assert res["name"] == "ControlTower-Runner"
    assert res["registry_match"] == "ControlTower-Runner"
    assert res["matched_alias"] == "controltowerrunner"


def test_resolve_local_identity_never_matches_by_role() -> None:
    """A node with role 'hub' must never borrow ControlTower's identity if its name doesn't match."""
    res = mr.resolve_local_identity("DeskComputer", "hub", _SAMPLE_REGISTRY)
    assert res["name"] == "DeskComputer"
    assert res["registry_match"] == "DeskComputer"
    assert res["registry_match"] != "ControlTower"


def test_resolve_local_identity_unregistered_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        res = mr.resolve_local_identity("MysteryLaptop", "node", _SAMPLE_REGISTRY)
    assert res["name"] == "MysteryLaptop"
    assert res["role"] == "node"
    assert res["registry_match"] == "unregistered"
    assert res["matched_alias"] is None
    assert any("MysteryLaptop" in record.message and "unregistered" in record.message for record in caplog.records)


def test_resolve_local_identity_env_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISPLAY_NAME", "OGLaptop")
    monkeypatch.setenv("MACHINE_ROLE", "node")
    res = mr.resolve_local_identity(None, None, _SAMPLE_REGISTRY)
    assert res["name"] == "OGLaptop"
    assert res["registry_match"] == "OGLaptop"


# ---------------------------------------------------------------------------
# API tests: GET /api/fleet/identity
# ---------------------------------------------------------------------------


def test_get_fleet_identity_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.include_router(orchestration_router)

    monkeypatch.setenv("DISPLAY_NAME", "DeskComputer")
    monkeypatch.setenv("MACHINE_ROLE", "node")
    monkeypatch.setattr(mr, "load_machine_registry", lambda: _SAMPLE_REGISTRY)

    client = TestClient(app)
    resp = client.get("/api/fleet/identity")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "DeskComputer"
    assert data["role"] == "node"
    assert data["registry_match"] == "DeskComputer"
    assert data["matched_alias"] == "deskcomputer"
    assert data["unregistered"] is False
    assert "deskcomputer" in data["aliases"]


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
            {"name": "DeskComputer", "url": "http://localhost:8321", "is_local": True, "online": True},
            {"name": "ControlTower", "url": "http://controltower:8321", "is_local": False, "online": True},
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
    proxy_mock = AsyncMock(return_value={"nodes": [{"name": "ControlTower", "is_local": True}]})
    monkeypatch.setattr(proxy_utils, "proxy_to_hub", proxy_mock)

    client = TestClient(app)
    resp = client.get("/api/fleet/nodes")
    assert resp.status_code == 200
    data = resp.json()

    # Must NOT have proxied to hub
    assert proxy_mock.call_count == 0
    # Must have served local payload where DeskComputer is local
    assert data["nodes"][0]["name"] == "DeskComputer"
    assert data["nodes"][0]["is_local"] is True


def test_merge_registry_suppresses_runner_pool_offline_when_parent_is_live() -> None:
    live = [
        {"name": "ControlTower", "url": "http://controltower.tail2bbcc7.ts.net:8321", "online": True, "is_local": True}
    ]
    merged = mr.merge_registry_with_live_nodes(live, _SAMPLE_REGISTRY)
    names = [n["name"] for n in merged]
    # ControlTower must be present
    assert "ControlTower" in names
    # ControlTower-Runner should NOT be appended as an offline duplicate
    assert "ControlTower-Runner" not in names


def test_merge_registry_deduplicates_local_and_remote() -> None:
    live = [
        {"name": "DeskComputer", "url": "http://localhost:8321", "is_local": True, "online": True},
        {
            "name": "DeskComputer",
            "url": "http://deskcomputer.tail2bbcc7.ts.net:8321",
            "is_local": False,
            "online": True,
        },
    ]
    merged = mr.merge_registry_with_live_nodes(live, _SAMPLE_REGISTRY)
    desk_entries = [n for n in merged if n["name"] == "DeskComputer"]
    # Exactly one entry for DeskComputer, and it must be marked is_local: True
    assert len(desk_entries) == 1
    assert desk_entries[0]["is_local"] is True
