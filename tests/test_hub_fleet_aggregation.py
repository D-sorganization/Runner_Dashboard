"""Tests for Hub dashboard telemetry aggregation across active machines (Issue #1169).

Verifies that:
1. Hub dashboard (/api/fleet/status) accurately aggregates all active nodes
   (node-a-Runner, hub-a, node-b) while ignoring retired pools
   (node-a-NVMe).
2. Offline / connection-refused nodes are gracefully classified as offline
   without blocking or dropping other healthy machines.
3. Startup assertions validate active pools and dashboard URLs, failing fast
   on duplicate ports or unresolvable URLs.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_MOCK_ACTIVE_FLEET_REGISTRY = {
    "version": 1,
    "machines": [
        {
            "name": "node-a",
            "aliases": ["node-a"],
            "role": "hub",
            "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
            "runner_pools": [
                {
                    "name": "node-a-NVMe",
                    "aliases": ["node-a-nvme"],
                    "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
                    "retired": True,
                    "retired_on": "2026-07-31",
                },
                {
                    "name": "node-a-Runner",
                    "aliases": ["node-a-runner"],
                    "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
                },
            ],
        },
        {
            "name": "hub-a",
            "aliases": ["hub-a"],
            "role": "node",
            "dashboard_url": "http://hub-a.fleet-example.ts.net:8321",
        },
        {
            "name": "node-b",
            "aliases": ["node-b"],
            "role": "node",
            "dashboard_url": "http://node-b.fleet-example.ts.net:8321",
        },
    ],
}


@pytest_asyncio.fixture
async def client(mock_auth: Any) -> AsyncGenerator[httpx.AsyncClient, None]:  # noqa: ARG001
    from httpx import ASGITransport, AsyncClient
    from server import app

    headers = {
        "Authorization": "Bearer test-key",
        "X-Requested-With": "XMLHttpRequest",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers,
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_hub_fleet_status_aggregates_all_active_machines(client: httpx.AsyncClient) -> None:
    """Hub should aggregate node-a-Runner, hub-a, and node-b.

    node-a-NVMe is retired and must not appear in the telemetry.
    """
    desk_telemetry = {
        "status": "online",
        "hostname": "hub-a",
        "cpu": {"percent": 12.5},
        "memory": {"percent": 45.0},
        "disk": {"percent": 60.0},
    }
    og_telemetry = {
        "status": "online",
        "hostname": "node-b",
        "cpu": {"percent": 25.0},
        "memory": {"percent": 55.0},
        "disk": {"percent": 70.0},
    }

    async def mock_get(url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", url)
        if "hub-a" in url:
            return httpx.Response(200, json=desk_telemetry, request=request)
        if "node-b" in url:
            return httpx.Response(200, json=og_telemetry, request=request)
        return httpx.Response(404, request=request)

    with (
        patch("dashboard_config.PORT", 8321),
        patch("dashboard_config.MACHINE_ROLE", "hub"),
        patch("routers.fleet.MACHINE_ROLE", "hub"),
        patch("routers.fleet.HOSTNAME", "node-a"),
        patch("routers.fleet.FLEET_NODES", {}),
        patch("routers.fleet.load_machine_registry", return_value=_MOCK_ACTIVE_FLEET_REGISTRY),
        patch.dict("os.environ", {"AUTODERIVE_FLEET_NODES": "1"}),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=mock_get)
        mock_client_cls.return_value = mock_client

        resp = await client.get("/api/fleet/status")
        assert resp.status_code == 200
        data = resp.json()

        # Exactly the 3 active machines/pools must be present
        assert "node-a-Runner" in data
        assert "hub-a" in data
        assert "node-b" in data

        # Retired pool must NOT be present
        assert "node-a-NVMe" not in data

        # Telemetry verification
        assert data["hub-a"]["cpu"]["percent"] == 12.5
        assert data["node-b"]["cpu"]["percent"] == 25.0
        assert data["node-a-Runner"]["_role"] == "hub"


@pytest.mark.asyncio
async def test_hub_fleet_status_handles_connection_refused_offline_node(client: httpx.AsyncClient) -> None:
    """When a node experiences connection refused, it is marked offline gracefully."""
    desk_telemetry = {
        "status": "online",
        "hostname": "hub-a",
        "cpu": {"percent": 14.0},
    }

    async def mock_get(url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", url)
        if "hub-a" in url:
            return httpx.Response(200, json=desk_telemetry, request=request)
        if "node-b" in url:
            raise httpx.ConnectError("Connection refused", request=request)
        return httpx.Response(404, request=request)

    with (
        patch("dashboard_config.PORT", 8321),
        patch("dashboard_config.MACHINE_ROLE", "hub"),
        patch("routers.fleet.MACHINE_ROLE", "hub"),
        patch("routers.fleet.HOSTNAME", "node-a"),
        patch("routers.fleet.FLEET_NODES", {}),
        patch("routers.fleet.load_machine_registry", return_value=_MOCK_ACTIVE_FLEET_REGISTRY),
        patch.dict("os.environ", {"AUTODERIVE_FLEET_NODES": "1"}),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=mock_get)
        mock_client_cls.return_value = mock_client

        resp = await client.get("/api/fleet/status")
        assert resp.status_code == 200
        data = resp.json()

        # All 3 nodes exist in response
        assert "node-a-Runner" in data
        assert "hub-a" in data
        assert "node-b" in data

        # hub-a is online
        assert data["hub-a"]["status"] == "online"

        # node-b is offline with refused reason
        assert data["node-b"]["status"] == "offline"
        assert data["node-b"]["offline_reason"] in ("refused", "wsl_connection_lost")


@pytest.mark.asyncio
async def test_startup_fails_fast_on_invalid_active_pool() -> None:
    """Server startup routine must fail fast if active pool in registry is invalid."""
    from server import _startup

    invalid_registry = {
        "machines": [
            {
                "name": "node-a",
                "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
                "runner_pools": [
                    {
                        "name": "node-a-Runner",
                        "dashboard_url": "",  # Empty URL on active pool
                    }
                ],
            }
        ]
    }

    with (
        patch("server.load_machine_registry", return_value=invalid_registry),
        patch("machine_registry.load_machine_registry", return_value=invalid_registry),
    ):
        with pytest.raises(RuntimeError, match="missing dashboard_url"):
            await _startup()
