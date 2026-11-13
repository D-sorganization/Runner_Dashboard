"""Regression test for the `?fields=compact` mode on `/api/fleet/nodes` (#3719).

The mobile Fleet view (frontend/src/pages/Fleet/Mobile.tsx) only reads
name/online/last_seen plus a handful of `system` scalars, but the default
`/api/fleet/nodes` response embeds the full `/api/system` and `/api/health`
payload for every node. Compact mode trims that for bandwidth-constrained
mobile polling without touching the default (desktop/orchestration) shape.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from routers.orchestration_node_routes import router as orchestration_router  # noqa: E402

_FULL_NODE = {
    "name": "node-a",
    "online": True,
    "last_seen": "2026-11-01T00:00:00Z",
    "hardware_specs": {"cpu_model": "big"},
    "workload_capacity": {"max_jobs": 4},
    "health": {"status": "healthy", "runners_registered": 3},
    "deployment": {"sha": "abc123"},
    "registry": {"display_name": "Node A"},
    "url": "http://node-a:8321",
    "system": {
        "hostname": "node-a",
        "uptime_seconds": 120,
        "cpu": {"percent_1m_avg": 10},
        "memory": {"total_gb": 32, "available_gb": 16},
        "hardware_specs": {"cpu_model": "big"},
        "disk": {"used_gb": 100},
    },
}

_FULL_PAYLOAD = {
    "nodes": [_FULL_NODE],
    "count": 1,
    "online_count": 1,
    "total_runners": 5,
}


def _client() -> TestClient:
    from routers.orchestration_node_routes import OrchestrationNodeDeps, orchestration_node_deps

    app = FastAPI()
    app.include_router(orchestration_router)
    deps = OrchestrationNodeDeps(
        get_fleet_nodes_impl=AsyncMock(return_value=_FULL_PAYLOAD),
        get_system_metrics_snapshot=AsyncMock(),
    )
    app.dependency_overrides[orchestration_node_deps] = lambda: deps
    return TestClient(app)


def test_fleet_nodes_default_response_is_unchanged() -> None:
    """No `fields` param must keep serving the full payload (existing consumers)."""
    resp = _client().get("/api/fleet/nodes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nodes"][0]["hardware_specs"] == {"cpu_model": "big"}
    assert data["nodes"][0]["health"]["status"] == "healthy"


def test_fleet_nodes_compact_strips_heavy_fields() -> None:
    """`?fields=compact` keeps only what the mobile Fleet view reads."""
    resp = _client().get("/api/fleet/nodes", params={"fields": "compact"})
    assert resp.status_code == 200
    data = resp.json()
    node = data["nodes"][0]
    assert set(node.keys()) == {"name", "online", "last_seen", "system"}
    assert node["name"] == "node-a"
    assert node["online"] is True
    assert node["last_seen"] == "2026-11-01T00:00:00Z"
    assert node["system"]["hostname"] == "node-a"
    assert node["system"]["uptime_seconds"] == 120
    assert node["system"]["cpu"] == {"percent_1m_avg": 10}
    assert node["system"]["memory"] == {"total_gb": 32, "available_gb": 16}
    assert "hardware_specs" not in node["system"]
    assert "disk" not in node["system"]
    # Top-level fleet counters are unaffected by compacting nodes.
    assert data["count"] == 1
    assert data["online_count"] == 1
