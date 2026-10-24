"""Tests for fleet status aggregator functionality (issue #753).

Updated for issue #942: split-pool topology is derived from
machine_registry.yml (via fleet_autoconfig.derive_pool_topology) instead of a
hardcoded ``if PORT == 8322`` branch. These tests patch the registry and the
local identity so the local/peer pool selection is deterministic and no longer
depends on a specific port literal.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# A two-pool node-a registry: NVMe on :8321, SSD on :8322.
_SPLIT_POOL_REGISTRY = {
    "version": 1,
    "machines": [
        {
            "name": "node-a",
            "aliases": ["node-a"],
            "dashboard_url": "http://localhost:8321",
            "runner_pools": [
                {
                    "name": "node-a-NVMe",
                    "aliases": ["node-a-nvme"],
                    "dashboard_url": "http://localhost:8321",
                },
                {
                    "name": "node-a-SSD",
                    "aliases": ["node-a-ssd"],
                    "dashboard_url": "http://localhost:8322",
                },
            ],
        },
    ],
}


@pytest_asyncio.fixture
async def client(mock_auth):  # noqa: ARG001
    from httpx import ASGITransport, AsyncClient  # noqa: PLC0415
    from server import app  # noqa: PLC0415

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


def _patch_topology(port: int = 8321):
    """Patch the env so this dashboard is the NVMe pool of the split registry."""
    return (
        patch("dashboard_config.PORT", port),
        patch("routers.fleet.HOSTNAME", "node-a-NVMe"),
        patch("server.FLEET_NODES", {}),
        patch("dashboard_config.FLEET_NODES", {}),
        patch("routers.fleet.FLEET_NODES", {}),
        patch("routers.fleet.load_machine_registry", return_value=_SPLIT_POOL_REGISTRY),
        patch("routers.fleet.should_proxy_fleet_to_hub", return_value=False),
        patch("routers.fleet.should_mark_hub_circuit_degraded", return_value=False),
    )


@pytest.mark.asyncio
async def test_fleet_status_exclude_pools(client) -> None:
    """When exclude_pools is True, the endpoint should not query the peer port."""
    p_port, p_host, p1, p2, p3, p_reg, p_proxy, p_circuit = _patch_topology()
    with p_port, p_host, p1, p2, p3, p_reg, p_proxy, p_circuit, patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock()
        mock_client_cls.return_value = mock_client

        resp = await client.get("/api/fleet/status?exclude_pools=true")
        assert resp.status_code == 200
        data = resp.json()

        # Local pool is the NVMe pool; the SSD peer must not be probed/listed.
        assert "node-a-NVMe" in data
        assert "node-a-SSD" not in data

        # Verify no requests were made to the peer.
        assert mock_client.get.call_count == 0


@pytest.mark.asyncio
async def test_fleet_status_aggregates_peer_successfully(client) -> None:
    """When exclude_pools is False, the endpoint should query the peer and merge."""
    p_port, p_host, p1, p2, p3, p_reg, p_proxy, p_circuit = _patch_topology()
    with p_port, p_host, p1, p2, p3, p_reg, p_proxy, p_circuit, patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "node-a-SSD": {
                "status": "online",
                "hostname": "node-a",
                "cpu": {"percent": 15.0},
            }
        }
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        resp = await client.get("/api/fleet/status")
        assert resp.status_code == 200
        data = resp.json()

        # Should contain both local (NVMe) and peer (SSD).
        assert "node-a-NVMe" in data
        assert "node-a-SSD" in data
        assert data["node-a-SSD"]["status"] == "online"

        # Verify the peer URL came from the registry's SSD dashboard_url.
        mock_client.get.assert_called_once()
        called_url = mock_client.get.call_args.args[0]
        assert called_url == "http://localhost:8322/api/fleet/status?exclude_pools=true"


@pytest.mark.asyncio
async def test_fleet_status_peer_offline_fallback(client) -> None:
    """When the peer is offline/unreachable, it should handle the exception gracefully."""
    p_port, p_host, p1, p2, p3, p_reg, p_proxy, p_circuit = _patch_topology()
    with p_port, p_host, p1, p2, p3, p_reg, p_proxy, p_circuit, patch("httpx.AsyncClient") as mock_client_cls:
        import httpx

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        # Force a ConnectError when trying to reach the peer.
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client_cls.return_value = mock_client

        resp = await client.get("/api/fleet/status")
        assert resp.status_code == 200
        data = resp.json()

        assert "node-a-NVMe" in data
        assert "node-a-SSD" in data
        assert data["node-a-SSD"]["status"] == "offline"
        assert data["node-a-SSD"]["offline_reason"] in ("refused", "wsl_connection_lost")


@pytest.mark.asyncio
async def test_fleet_status_marks_hub_circuit_fallback_degraded(client) -> None:
    p_port, p_host, p1, p2, p3, p_reg, p_proxy, p_circuit = _patch_topology()
    with (
        p_port,
        p_host,
        p1,
        p2,
        p3,
        p_reg,
        p_proxy,
        p_circuit,
        patch("routers.fleet.MACHINE_ROLE", "node"),
        patch("routers.fleet.should_mark_hub_circuit_degraded", return_value=True),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock()
        mock_client_cls.return_value = mock_client

        resp = await client.get("/api/fleet/status?exclude_pools=true")

    assert resp.status_code == 200
    assert resp.headers["X-Dashboard-Degraded"] == "hub-circuit-open"
    assert resp.json()["_degraded"] is True


@pytest.mark.asyncio
async def test_fleet_status_keeps_explicit_local_response_plain(client) -> None:
    p_port, p_host, p1, p2, p3, p_reg, p_proxy, p_circuit = _patch_topology()
    with (
        p_port,
        p_host,
        p1,
        p2,
        p3,
        p_reg,
        p_proxy,
        p_circuit,
        patch("routers.fleet.MACHINE_ROLE", "node"),
        patch("routers.fleet.should_mark_hub_circuit_degraded", return_value=False),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock()
        mock_client_cls.return_value = mock_client

        resp = await client.get("/api/fleet/status?exclude_pools=true&local=true")

    assert resp.status_code == 200
    assert "X-Dashboard-Degraded" not in resp.headers
    assert "_degraded" not in resp.json()


@pytest.mark.asyncio
async def test_fleet_status_exclude_pools_skips_hub_proxy_and_remote_nodes(client) -> None:
    p_port, p_host, p1, p2, _, p_reg, _, p_circuit = _patch_topology()
    with (
        p_port,
        p_host,
        p1,
        p2,
        p_reg,
        p_circuit,
        patch("routers.fleet.FLEET_NODES", {"RemoteNode": "http://10.0.0.9:8321"}),
        patch("routers.fleet.should_proxy_fleet_to_hub", return_value=True),
        patch("routers.fleet.proxy_to_hub", new_callable=AsyncMock) as mock_proxy,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock()
        mock_client_cls.return_value = mock_client

        resp = await client.get("/api/fleet/status?exclude_pools=true")

    assert resp.status_code == 200
    mock_proxy.assert_not_called()
    assert mock_client.get.call_count == 0
    assert "node-a-NVMe" in resp.json()
