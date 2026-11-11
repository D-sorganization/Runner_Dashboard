"""Host metrics provider interface (#4309); dashboard consumes cache, never probes directly."""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable


def normalize_host_key(value: str) -> str:
    """Stable host token for cache lookup and gauge caps (#4309 / #4310)."""
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


@runtime_checkable
class HostMetricsProvider(Protocol):
    def read_cached(self, host: str) -> dict[str, Any] | None:
        """Return the latest cached host metrics snapshot for ``host``, or ``None``."""


class StubHostMetricsProvider:
    """Fallback when the cache reader cannot be imported."""

    def read_cached(self, host: str) -> dict[str, Any] | None:  # noqa: ARG002
        return None


def get_host_metrics_provider() -> HostMetricsProvider:
    try:
        from host_metrics_cache import CachedHostMetricsProvider  # noqa: PLC0415

        return CachedHostMetricsProvider()
    except ImportError:
        return StubHostMetricsProvider()
