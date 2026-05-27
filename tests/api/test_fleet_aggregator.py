"""Tests for fleet status aggregator functionality (issue #753)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


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


@pytest.mark.asyncio
async def test_fleet_status_exclude_pools(client) -> None:
    """When exclude_pools is True, the endpoint should not query the peer port."""
    with (
        patch("dashboard_config.PORT", 8321),
        patch("server.FLEET_NODES", {}),
        patch("dashboard_config.FLEET_NODES", {}),
        patch("routers.fleet.FLEET_NODES", {}),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        # Mock client so we can assert it was NOT called
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock()
        mock_client_cls.return_value = mock_client

        resp = await client.get("/api/fleet/status?exclude_pools=true")
        assert resp.status_code == 200
        data = resp.json()

        # Local pool name should be ControlTower-NVMe since PORT is 8321
        assert "ControlTower-NVMe" in data
        assert "ControlTower-HDD" not in data

        # Verify no requests were made to peer
        assert mock_client.get.call_count == 0


@pytest.mark.asyncio
async def test_fleet_status_aggregates_peer_successfully(client) -> None:
    """When exclude_pools is False, the endpoint should query the peer and merge."""
    with (
        patch("dashboard_config.PORT", 8321),
        patch("server.FLEET_NODES", {}),
        patch("dashboard_config.FLEET_NODES", {}),
        patch("routers.fleet.FLEET_NODES", {}),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ControlTower-HDD": {
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

        # Should contain both local (NVMe) and peer (HDD)
        assert "ControlTower-NVMe" in data
        assert "ControlTower-HDD" in data
        assert data["ControlTower-HDD"]["status"] == "online"

        # Verify the get was called with the correct peer url
        mock_client.get.assert_called_once_with(
            "http://localhost:8322/api/fleet/status?exclude_pools=true",
            timeout=5,
        )


@pytest.mark.asyncio
async def test_fleet_status_peer_offline_fallback(client) -> None:
    """When the peer is offline/unreachable, it should handle the exception gracefully."""
    with (
        patch("dashboard_config.PORT", 8321),
        patch("server.FLEET_NODES", {}),
        patch("dashboard_config.FLEET_NODES", {}),
        patch("routers.fleet.FLEET_NODES", {}),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        import httpx

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        # Force a ConnectError when trying to reach peer
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client_cls.return_value = mock_client

        resp = await client.get("/api/fleet/status")
        assert resp.status_code == 200
        data = resp.json()

        assert "ControlTower-NVMe" in data
        assert "ControlTower-HDD" in data
        assert data["ControlTower-HDD"]["status"] == "offline"
        assert data["ControlTower-HDD"]["offline_reason"] in ("refused", "wsl_connection_lost")
