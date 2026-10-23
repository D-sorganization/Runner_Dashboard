"""Fleet status must stay within a wall-clock bound when a peer is slow (#2110)."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import cache_utils  # noqa: E402
import fleet_status_cache as fsc  # noqa: E402
import routers.fleet as fleet  # noqa: E402
from dashboard_config import HttpTimeout  # noqa: E402
from fleet_status_cache import fleet_status_cache_key, serve_fleet_status  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_fleet_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_utils, "_main_cache", cache_utils.Cache("fleet-status-bound"))
    monkeypatch.setattr(cache_utils, "_swr_refreshes", {})


@pytest.mark.asyncio
async def test_slow_peer_does_not_exceed_probe_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    delay = HttpTimeout.FLEET_NODE_PROBE_READ_S + 5.0
    started: list[float] = []

    class _Slow:
        async def __aenter__(self) -> _Slow:
            return self

        async def __aexit__(self, *_a: object) -> bool:
            return False

        async def get(self, *_a: object, **kwargs: object) -> object:
            started.append(time.monotonic())
            timeout = kwargs.get("timeout")
            read_s = float(
                getattr(timeout, "read", HttpTimeout.FLEET_NODE_PROBE_READ_S) or HttpTimeout.FLEET_NODE_PROBE_READ_S
            )
            await asyncio.sleep(read_s + 0.15)
            raise httpx.ReadTimeout("simulated slow peer")

    monkeypatch.setattr(fleet.httpx, "AsyncClient", lambda *_a, **_k: _Slow())
    monkeypatch.setattr(fleet, "should_proxy_fleet_to_hub", lambda _r: False)
    monkeypatch.setattr(fleet, "should_mark_hub_circuit_degraded", lambda _r: False)
    monkeypatch.setattr(fleet, "load_machine_registry", lambda: {})
    monkeypatch.setattr(fleet, "derive_pool_topology", lambda *_a, **_k: (None, []))
    monkeypatch.setattr(fleet, "FLEET_NODES", {"slow-node": "http://slow:8321"})
    monkeypatch.setattr(fleet, "_record_fleet_events", lambda _r: None)

    async def _metrics() -> dict:
        return {"hostname": "local"}

    monkeypatch.setattr(fleet, "get_system_metrics_snapshot", _metrics)

    from fastapi import Response

    class _Req:
        pass

    wall_budget = HttpTimeout.FLEET_NODE_PROBE_READ_S + 1.5
    t0 = time.monotonic()
    payload = await asyncio.wait_for(fleet.get_fleet_status(_Req(), Response()), timeout=wall_budget)
    elapsed = time.monotonic() - t0

    assert elapsed < wall_budget
    assert payload["slow-node"]["status"] == "offline"
    assert len(started) == 1


@pytest.mark.asyncio
async def test_stale_flag_while_refreshing(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_status_cache import FLEET_STATUS_FRESH_TTL_S, FLEET_STATUS_STALE_TTL_S

    cache_key = fleet_status_cache_key(exclude_pools=True)
    cached = {"DeskComputer": {"hostname": "cached"}}
    cache_utils.cache_set(cache_key, cached)

    def _tiered_get(key: str, ttl: float):
        if key != cache_key:
            return cache_utils.cache_get(key, ttl)
        if ttl <= FLEET_STATUS_FRESH_TTL_S:
            return None
        if ttl <= FLEET_STATUS_STALE_TTL_S:
            return cached
        return None

    monkeypatch.setattr(fsc, "cache_get", _tiered_get)
    monkeypatch.setattr(cache_utils, "cache_get", _tiered_get)

    async def slow_collect() -> dict:
        await asyncio.sleep(0.15)
        return {"DeskComputer": {"hostname": "fresh"}}

    served = await serve_fleet_status(cache_key, slow_collect, wait_timeout=0.01)
    assert served.get("_stale") is True
    assert served["DeskComputer"]["hostname"] == "cached"
