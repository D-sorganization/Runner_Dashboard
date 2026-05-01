"""Pooled HTTP clients for GitHub API, Maxwell, and fleet operations.

This module provides connection-pooled httpx.AsyncClient instances that are
constructed at application startup and torn down at shutdown. Using pooled
clients reduces latency by reusing TCP/TLS connections instead of opening
new connections for every request.

See issue #364 for motivation and acceptance criteria.
"""

from __future__ import annotations

import httpx
from dashboard_config.timeouts import HttpTimeout


class HttpClients:
    """Container for pooled HTTP clients.

    Clients are constructed with connection pooling limits:
    - max_keepalive_connections=20: Keep up to 20 idle connections per host
    - max_connections=100: Allow up to 100 total connections

    Timeouts are sourced from dashboard_config.timeouts.HttpTimeout.
    """

    def __init__(self) -> None:
        # GitHub API client - uses proxy timeout for hub calls
        self.gh_client = httpx.AsyncClient(
            timeout=HttpTimeout.PROXY_TO_HUB_S,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )

        # Maxwell daemon client - short timeout for local daemon health checks
        self.maxwell_client = httpx.AsyncClient(
            timeout=HttpTimeout.MAXWELL_PROXY_S,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )

        # Fleet client - for cross-node system probes
        self.fleet_client = httpx.AsyncClient(
            timeout=HttpTimeout.PROXY_NODE_SYSTEM_S,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )

    async def close(self) -> None:
        """Close all HTTP clients.

        Should be called during application shutdown to release resources.
        """
        await self.gh_client.aclose()
        await self.maxwell_client.aclose()
        await self.fleet_client.aclose()


# Global instance - set by FastAPI lifespan events
_http_clients: HttpClients | None = None


def get_http_clients() -> HttpClients:
    """Get the global HTTP clients instance.

    Returns:
        HttpClients instance with pooled clients

    Raises:
        RuntimeError: If clients haven't been initialized yet
    """
    if _http_clients is None:
        raise RuntimeError(
            "HTTP clients not initialized. Ensure initialize_http_clients() "
            "is called during application startup."
        )
    return _http_clients


def initialize_http_clients() -> HttpClients:
    """Initialize the global HTTP clients instance.

    Returns:
        HttpClients instance with pooled clients
    """
    global _http_clients
    _http_clients = HttpClients()
    return _http_clients


async def shutdown_http_clients() -> None:
    """Shutdown and close all HTTP clients.

    Should be called during application shutdown.
    """
    global _http_clients
    if _http_clients is not None:
        await _http_clients.close()
        _http_clients = None
