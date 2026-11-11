"""Tests for host_metrics stub provider (#4310 / #4309)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import host_metrics as hm  # noqa: E402


def test_stub_provider_returns_none() -> None:
    provider = hm.StubHostMetricsProvider()
    assert provider.read_cached("hub-a") is None


def test_get_host_metrics_provider_falls_back_to_stub() -> None:
    provider = hm.get_host_metrics_provider()
    assert provider.read_cached("node-b") is None
