"""Regression tests for cached host metrics (#4309)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import host_metrics_cache as hmc
import pytest


def _fresh_payload(*, windows_memory_percent: float = 83.0, wsl_used_gb: float = 3.0) -> dict:
    return {
        "schema_version": 1,
        "sampled_at": datetime.now(UTC).isoformat(),
        "hostname": "desk",
        "windows": {
            "cpu_percent": 61.0,
            "memory_total_gb": 128.0,
            "memory_used_gb": 106.4,
            "memory_available_gb": 21.6,
            "memory_percent": windows_memory_percent,
            "vmmemwsl_working_set_gb": 24.0,
        },
        "wsl": {
            "mem_available_gb": 6.0,
            "memory_used_gb": wsl_used_gb,
            "psi": {"memory_some_avg60": 5.0, "cpu_some_avg60": 2.0},
        },
        "disk": {"by_path": {"/mnt/c": {"free_gb": 120.0, "path": "/mnt/c"}}},
    }


def test_read_cache_fresh_and_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hmc, "cache_file_path", lambda: tmp_path / "host_metrics.json")
    hmc.write_cache_atomic(_fresh_payload())
    fresh = hmc.read_cache(stale_after_s=90)
    assert fresh.status == "fresh"
    assert fresh.is_usable

    stale_payload = _fresh_payload()
    stale_payload["sampled_at"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    hmc.write_cache_atomic(stale_payload)
    stale = hmc.read_cache(stale_after_s=90)
    assert stale.status == "stale"
    assert not stale.is_usable


def test_windows_host_from_cache_returns_none_when_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hmc, "cache_file_path", lambda: tmp_path / "host_metrics.json")
    payload = _fresh_payload()
    payload["sampled_at"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    hmc.write_cache_atomic(payload)
    assert hmc.windows_host_from_cache() is None


def test_desk_nov10_case_surfaces_windows_ram_not_wsl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows RAM ~83% while WSL used ~3 GB must be visible from cache, not masked as healthy WSL."""
    monkeypatch.setattr(hmc, "cache_file_path", lambda: tmp_path / "host_metrics.json")
    hmc.write_cache_atomic(_fresh_payload(windows_memory_percent=82.9, wsl_used_gb=3.0))
    result = hmc.read_cache()
    view = hmc.metrics_view_for_gauge(result)
    assert view is not None
    assert view["memory"]["percent"] == pytest.approx(82.9)
    assert view["memory"]["wsl_used_gb"] == pytest.approx(3.0)
    assert view["memory"]["vmmemwsl_working_set_gb"] == pytest.approx(24.0)


def test_sample_host_metrics_never_called_from_read_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hmc, "cache_file_path", lambda: tmp_path / "host_metrics.json")
    hmc.write_cache_atomic(_fresh_payload())
    with patch.object(hmc, "sample_host_metrics", side_effect=AssertionError("no live probe on read")):
        assert hmc.windows_host_from_cache() is not None


def test_cached_provider_unknown_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hmc, "cache_file_path", lambda: Path("/nonexistent/host_metrics.json"))
    monkeypatch.setattr(hmc, "HOSTNAME", "desk")
    provider = hmc.CachedHostMetricsProvider()
    out = provider.read_cached("desk")
    assert out is not None
    assert out.get("status") == "unknown"


def test_fresh_cache_without_windows_reports_degraded_on_wsl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed Windows probe must not surface as status ok with null CPU/RAM (#4309)."""
    monkeypatch.setattr(hmc, "cache_file_path", lambda: tmp_path / "host_metrics.json")
    monkeypatch.setattr(hmc, "_expects_windows_host_metrics", lambda: True)
    payload = _fresh_payload()
    payload["windows"] = None
    hmc.write_cache_atomic(payload)
    result = hmc.read_cache()
    assert result.is_usable
    view = hmc.metrics_view_for_gauge(result)
    assert view is not None
    assert view["status"] == "degraded"
    assert view["cpu"]["percent"] is None
    assert view["memory"]["percent"] is None


def test_scheduler_cache_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hmc, "cache_file_path", lambda: tmp_path / "host_metrics.json")
    hmc.write_cache_atomic(_fresh_payload())
    assert hmc.scheduler_cached_disk_free_gb("/mnt/c") == pytest.approx(120.0)
    assert hmc.scheduler_cached_memory_psi_avg60() == pytest.approx(5.0)
