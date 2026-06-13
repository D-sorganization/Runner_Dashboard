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

# A two-pool ControlTower registry: NVMe on :8321, SSD on :8322.
_SPLIT_POOL_REGISTRY = {
    "version": 1,
    "machines": [
        {
            "name": "ControlTower",
            "aliases": ["controltower"],
            "dashboard_url": "http://localhost:8321",
            "runner_pools": [
                {
                    "name": "ControlTower-NVMe",
                    "aliases": ["controltower-nvme"],
                    "dashboard_url": "http://localhost:8321",
                },
                {
                    "name": "ControlTower-SSD",
                    "aliases": ["controltower-ssd"],
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
        patch("routers.fleet.HOSTNAME", "ControlTower-NVMe"),
        patch("server.FLEET_NODES", {}),
        patch("dashboard_config.FLEET_NODES", {}),
        patch("routers.fleet.FLEET_NODES", {}),
        patch("routers.fleet.load_machine_registry", return_value=_SPLIT_POOL_REGISTRY),
    )


@pytest.mark.asyncio
async def test_fleet_status_exclude_pools(client) -> None:
    """When exclude_pools is True, the endpoint should not query the peer port."""
    p_port, p_host, p1, p2, p3, p_reg = _patch_topology()
    with p_port, p_host, p1, p2, p3, p_reg, patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock()
        mock_client_cls.return_value = mock_client

        resp = await client.get("/api/fleet/status?exclude_pools=true")
        assert resp.status_code == 200
        data = resp.json()

        # Local pool is the NVMe pool; the SSD peer must not be probed/listed.
        assert "ControlTower-NVMe" in data
        assert "ControlTower-SSD" not in data

        # Verify no requests were made to the peer.
        assert mock_client.get.call_count == 0


@pytest.mark.asyncio
async def test_fleet_status_aggregates_peer_successfully(client) -> None:
    """When exclude_pools is False, the endpoint should query the peer and merge."""
    p_port, p_host, p1, p2, p3, p_reg = _patch_topology()
    with p_port, p_host, p1, p2, p3, p_reg, patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ControlTower-SSD": {
                "status": "online",
                "hostname": "ControlTower",
                "cpu": {"percent": 15.0},
            }
        }
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        resp = await client.get("/api/fleet/status")
        assert resp.status_code == 200
        data = resp.json()

        # Should contain both local (NVMe) and peer (SSD).
        assert "ControlTower-NVMe" in data
        assert "ControlTower-SSD" in data
        assert data["ControlTower-SSD"]["status"] == "online"

        # Verify the peer URL came from the registry's SSD dashboard_url.
        mock_client.get.assert_called_once()
        called_url = mock_client.get.call_args.args[0]
        assert called_url == "http://localhost:8322/api/fleet/status?exclude_pools=true"


@pytest.mark.asyncio
async def test_fleet_status_peer_offline_fallback(client) -> None:
    """When the peer is offline/unreachable, it should handle the exception gracefully."""
    p_port, p_host, p1, p2, p3, p_reg = _patch_topology()
    with p_port, p_host, p1, p2, p3, p_reg, patch("httpx.AsyncClient") as mock_client_cls:
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

        assert "ControlTower-NVMe" in data
        assert "ControlTower-SSD" in data
        assert data["ControlTower-SSD"]["status"] == "offline"
        assert data["ControlTower-SSD"]["offline_reason"] in ("refused", "wsl_connection_lost")
