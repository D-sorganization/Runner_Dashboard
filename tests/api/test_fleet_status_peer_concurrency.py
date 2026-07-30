"""Regression: /api/fleet/status probes peers concurrently with a bounded connect.

A single unreachable peer pool used to stall the whole endpoint for its full
30 s read budget because the peer loop was sequential and the connect phase was
unbounded. These tests pin the two fixes: a connect-capped timeout and a
concurrent (gather) peer fan-out.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import routers.fleet as fleet  # noqa: E402
from dashboard_config import HttpTimeout  # noqa: E402


def test_node_probe_timeout_caps_connect() -> None:
    timeout = fleet._node_probe_timeout()
    assert timeout.connect == HttpTimeout.NODE_CONNECT_S
    assert timeout.read == HttpTimeout.PROXY_NODE_SYSTEM_S
    # Dead hosts fail fast on connect; slow-but-alive hosts keep the read budget.
    assert timeout.connect < timeout.read


@pytest.mark.asyncio
async def test_fetch_peer_pool_classifies_unreachable_as_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        async def __aenter__(self) -> _Boom:
            return self

        async def __aexit__(self, *_a: object) -> bool:
            return False

        async def get(self, *_a: object, **_k: object) -> object:
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(fleet.httpx, "AsyncClient", lambda *_a, **_k: _Boom())

    result = await fleet._fetch_peer_pool({"name": "DeskComputer", "url": "http://dead:8321"})

    assert result["DeskComputer"]["status"] == "offline"


@pytest.mark.asyncio
async def test_fleet_status_queries_peer_pools_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    # A barrier of 2 only releases once BOTH peer fetches are in-flight. The old
    # sequential loop would start peer A, block here forever (B never starts),
    # and time out; the gather-based fan-out lets both arrive and complete.
    barrier = asyncio.Barrier(2)

    class _Gated:
        async def __aenter__(self) -> _Gated:
            return self

        async def __aexit__(self, *_a: object) -> bool:
            return False

        async def get(self, *_a: object, **_k: object) -> object:
            await barrier.wait()

            class _Resp:
                status_code = 200

                def json(self) -> dict:
                    return {}

            return _Resp()

    monkeypatch.setattr(fleet.httpx, "AsyncClient", lambda *_a, **_k: _Gated())
    monkeypatch.setattr(fleet, "should_proxy_fleet_to_hub", lambda _r: False)
    monkeypatch.setattr(fleet, "should_mark_hub_circuit_degraded", lambda _r: False)

    async def _metrics() -> dict:
        return {"hostname": "local"}

    monkeypatch.setattr(fleet, "get_system_metrics_snapshot", _metrics)
    monkeypatch.setattr(fleet, "load_machine_registry", lambda: {})
    monkeypatch.setattr(
        fleet,
        "derive_pool_topology",
        lambda *_a, **_k: (
            None,
            [{"name": "A", "url": "http://a:8321"}, {"name": "B", "url": "http://b:8321"}],
        ),
    )
    monkeypatch.setattr(fleet, "FLEET_NODES", {})
    monkeypatch.setattr(fleet, "_record_fleet_events", lambda _r: None)

    from fastapi import Response

    class _Req:
        pass

    # Completes only if the two peers run concurrently; otherwise wait_for fires.
    await asyncio.wait_for(fleet.get_fleet_status(_Req(), Response()), timeout=3.0)
