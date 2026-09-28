"""Tests for concurrent collection and single-flight in fleet nodes.

Issue #1750:
- In `_collect_live_fleet_nodes`, run local system and local health concurrently
  with remote `fetch_node` probes.
- Add single-flight guard to `_get_fleet_nodes_impl`: concurrent callers await
  the one in-flight collection.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import server  # noqa: E402


@pytest.mark.asyncio
async def test_collect_live_fleet_nodes_runs_local_and_remote_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local collection and remote node probing must overlap concurrently.

    A slow local probe must not delay starting remote probes. A barrier of 2
    ensures both local and remote collection must be concurrently in-flight to
    proceed.
    """
    barrier = asyncio.Barrier(2)
    order_log: list[str] = []

    async def fake_system() -> dict:
        order_log.append("local_sys_start")
        await barrier.wait()
        order_log.append("local_sys_finish")
        return {
            "hostname": "ControlTower-NVMe",
            "hardware_specs": {},
            "workload_capacity": {},
        }

    async def fake_health() -> dict:
        return {"status": "healthy", "runners_registered": 4}

    class _MockResponse:
        status_code = 200

        def json(self) -> dict:
            return {"status": "healthy", "runners_registered": 2}

    class _MockClient:
        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def get(self, url: str) -> _MockResponse:
            if "/api/system" in url:
                order_log.append("remote_probe_start")
                await barrier.wait()
                order_log.append("remote_probe_finish")
            return _MockResponse()

    monkeypatch.setattr(server._system_router, "get_system_metrics", fake_system)
    monkeypatch.setattr(server._health_router, "_health_impl", fake_health)
    monkeypatch.setattr(server, "FLEET_NODES", {"RemoteDesk": "http://100.64.0.2:8321"})
    monkeypatch.setattr(server.httpx, "AsyncClient", lambda **_kw: _MockClient())

    nodes = await asyncio.wait_for(server._collect_live_fleet_nodes(), timeout=2.0)

    assert len(nodes) == 2
    assert "local_sys_start" in order_log
    assert "remote_probe_start" in order_log
    # Both started before either finished because of the barrier
    assert order_log.index("remote_probe_start") < order_log.index("local_sys_finish")


@pytest.mark.asyncio
async def test_get_fleet_nodes_single_flight_coalesces_concurrent_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent callers to _get_fleet_nodes_impl await the same in-flight task."""
    collect_calls = 0
    collect_event = asyncio.Event()

    async def slow_collect() -> list[dict]:
        nonlocal collect_calls
        collect_calls += 1
        await collect_event.wait()
        return [
            {
                "name": "ControlTower-NVMe",
                "url": "http://localhost:8321",
                "online": True,
                "dashboard_reachable": True,
                "is_local": True,
                "role": "hub",
                "system": {},
                "health": {"runners_registered": 8},
                "last_seen": "2026-09-28T16:00:00Z",
                "error": None,
            }
        ]

    # Ensure empty cache at start
    monkeypatch.setattr(server, "_cache_get", lambda key, ttl: None)
    cached_set: list[dict] = []
    monkeypatch.setattr(server, "_cache_set", lambda key, val: cached_set.append(val))
    monkeypatch.setattr(server, "_collect_live_fleet_nodes", slow_collect)
    monkeypatch.setattr(server, "load_machine_registry", lambda: {"version": 1, "machines": []})
    monkeypatch.setattr(server, "merge_registry_with_live_nodes", lambda nodes, registry: nodes)
    monkeypatch.setattr(server, "_node_visibility_snapshot", lambda node: {})

    # Launch 5 concurrent calls
    tasks = [asyncio.create_task(server._get_fleet_nodes_impl()) for _ in range(5)]

    # Give event loop a cycle to enter all 5 tasks into single-flight
    await asyncio.sleep(0.01)

    # Release slow collection
    collect_event.set()

    results = await asyncio.gather(*tasks)

    # All 5 callers get identical response
    assert len(results) == 5
    for r in results:
        assert r["count"] == 1
        assert r["nodes"][0]["name"] == "ControlTower-NVMe"

    # Crucially, collection was triggered EXACTLY ONCE
    assert collect_calls == 1
