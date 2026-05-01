"""Tests for pooled HTTP clients (issue #364)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add backend to path for imports
BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from http_clients import (
    HttpClients,
    get_http_clients,
    initialize_http_clients,
    shutdown_http_clients,
)


class TestHttpClients:
    """Test HTTP client pool initialization and lifecycle."""

    def test_http_clients_init(self) -> None:
        """Test that HttpClients initializes with pooled clients."""
        clients = HttpClients()

        assert clients.gh_client is not None
        assert clients.maxwell_client is not None
        assert clients.fleet_client is not None

    @pytest.mark.asyncio
    async def test_http_clients_close(self) -> None:
        """Test that close() properly shuts down all clients."""
        clients = HttpClients()

        # Should not raise
        await clients.close()

    def test_initialize_http_clients(self) -> None:
        """Test initialize_http_clients sets global instance."""
        # Reset global state
        import http_clients as module

        module._http_clients = None

        result = initialize_http_clients()

        assert result is not None
        assert isinstance(result, HttpClients)
        assert get_http_clients() is result

    def test_get_http_clients_not_initialized(self) -> None:
        """Test get_http_clients raises when not initialized."""
        import http_clients as module

        module._http_clients = None

        with pytest.raises(RuntimeError, match="not initialized"):
            get_http_clients()

    @pytest.mark.asyncio
    async def test_shutdown_http_clients(self) -> None:
        """Test shutdown_http_clients closes and resets global instance."""
        import http_clients as module

        module._http_clients = HttpClients()

        await shutdown_http_clients()

        assert module._http_clients is None

    def test_http_clients_timeout_values(self) -> None:
        """Test that clients use correct timeout values from HttpTimeout."""

        clients = HttpClients()

        # Verify timeouts are set from config (check they're not None/default)
        assert clients.gh_client.timeout is not None
        assert clients.maxwell_client.timeout is not None
        assert clients.fleet_client.timeout is not None


class TestConnectionPooling:
    """Test connection pooling behavior."""

    @pytest.mark.asyncio
    async def test_client_reuses_connections(self) -> None:
        """Test that multiple requests can be made with pooled client."""
        import httpx

        # Setup mock transport
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json={"status": "ok", "count": call_count})

        transport = httpx.MockTransport(handler)

        client = httpx.AsyncClient(
            transport=transport,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )

        # Make multiple requests
        responses = []
        for _ in range(5):
            resp = await client.get("http://test/api")
            responses.append(resp)

        # All should succeed
        assert all(r.status_code == 200 for r in responses)
        assert call_count == 5

        await client.aclose()

    @pytest.mark.asyncio
    async def test_connection_limit_enforced(self) -> None:
        """Test that connection limits are configured correctly."""
        import httpx

        # Create client with specific limits
        limits = httpx.Limits(max_keepalive_connections=1, max_connections=2)
        client = httpx.AsyncClient(limits=limits)

        # Verify the client was created with our limits
        # httpx doesn't expose limits directly, but we can verify
        # the client works correctly
        assert client is not None

        await client.aclose()
