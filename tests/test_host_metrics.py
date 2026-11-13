"""Tests for host_metrics provider (#4309, #4310)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import host_metrics as hm  # noqa: E402
from host_metrics import StubHostMetricsProvider, get_host_metrics_provider


def test_stub_provider_returns_none() -> None:
    provider = hm.StubHostMetricsProvider()
    assert provider.read_cached("hub-a") is None


def test_get_host_metrics_provider_returns_cached_implementation() -> None:
    provider = get_host_metrics_provider()
    assert not isinstance(provider, StubHostMetricsProvider)
    assert hasattr(provider, "read_cached")
