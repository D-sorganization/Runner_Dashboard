"""Tests for host_metrics provider facade (#4309)."""

from __future__ import annotations

from host_metrics import StubHostMetricsProvider, get_host_metrics_provider


def test_get_host_metrics_provider_returns_cached_implementation() -> None:
    provider = get_host_metrics_provider()
    assert not isinstance(provider, StubHostMetricsProvider)
    assert hasattr(provider, "read_cached")
