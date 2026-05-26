"""Regression tests for partial /api/fleet/nodes responses."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import server  # noqa: E402


@pytest.mark.asyncio
async def test_fleet_nodes_returns_local_node_when_collection_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A remote/federation failure must not make Machine Health render 0/0."""

    async def broken_collection() -> list[dict]:
        raise TimeoutError("remote fanout timed out")

    async def fake_system() -> dict:
        return {
            "hostname": "ControlTower-NVMe",
            "hardware_specs": {},
            "workload_capacity": {},
        }

    async def fake_health() -> dict:
        return {"status": "healthy", "runners_registered": 8}

    monkeypatch.setattr(server, "_cache_get", lambda key, ttl: None)
    monkeypatch.setattr(server, "_cache_set", lambda key, value: None)
    monkeypatch.setattr(server, "_collect_live_fleet_nodes", broken_collection)
    monkeypatch.setattr(server, "get_system_metrics_snapshot", fake_system)
    monkeypatch.setattr(server._health_router, "_health_impl", fake_health)
    monkeypatch.setattr(server, "load_machine_registry", lambda: {"version": 1, "machines": []})
    monkeypatch.setattr(server, "merge_registry_with_live_nodes", lambda nodes, registry: nodes)
    monkeypatch.setattr(server, "_node_visibility_snapshot", lambda node: {})

    result = await server._get_fleet_nodes_impl()

    assert result["count"] == 1
    assert result["nodes"][0]["is_local"] is True
    assert result["nodes"][0]["health"]["runners_registered"] == 8
    assert result["partial"] is True
    assert result["degraded"] is True
    assert "remote fanout timed out" in result["fleet_probe_error"]
