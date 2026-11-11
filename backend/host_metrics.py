"""Host metrics provider interface (#4309); dashboard consumes cache, never probes directly."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class HostMetricsProvider(Protocol):
    def read_cached(self, host: str) -> dict[str, Any] | None:
        """Return the latest cached host metrics snapshot for ``host``, or ``None``."""


class StubHostMetricsProvider:
    """Fallback until #4309 lands; callers use WSL /api/system telemetry instead."""

    def read_cached(self, host: str) -> dict[str, Any] | None:  # noqa: ARG002
        return None


def get_host_metrics_provider() -> HostMetricsProvider:
    try:
        from host_metrics_cache import CachedHostMetricsProvider  # noqa: PLC0415

        return CachedHostMetricsProvider()
    except ImportError:
        return StubHostMetricsProvider()
