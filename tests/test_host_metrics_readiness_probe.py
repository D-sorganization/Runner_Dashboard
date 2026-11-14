"""Readiness and startup contracts for host-metrics cache health (#4443)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from datetime import UTC, datetime

import host_metrics_cache as hmc  # noqa: E402
import readiness as r  # noqa: E402


@pytest.mark.asyncio
async def test_host_metrics_cache_probe_ok_when_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hmc, "cache_file_path", lambda: tmp_path / "host_metrics.json")
    hmc.write_cache_atomic(
        {"sampled_at": datetime.now(UTC).replace(microsecond=0).isoformat(), "schema_version": 1},
        path=tmp_path / "host_metrics.json",
    )
    probe = r.HostMetricsCacheProbe(dashboard_service_check=lambda: True)
    status, detail = await probe.check()
    assert status == "ok"
    assert detail is None


@pytest.mark.asyncio
async def test_host_metrics_cache_probe_degraded_when_missing_on_deployed_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = r.HostMetricsCacheProbe(dashboard_service_check=lambda: True)
    with patch.object(hmc, "read_cache", return_value=hmc.CacheReadResult("missing", None, None, Path("/x"))):
        status, detail = await probe.check()
    assert status == "degraded"
    assert detail is not None
    assert "install-host-metrics-probe" in detail


@pytest.mark.asyncio
async def test_host_metrics_cache_probe_skips_when_dashboard_not_deployed() -> None:
    probe = r.HostMetricsCacheProbe(dashboard_service_check=lambda: False)
    status, detail = await probe.check()
    assert status == "ok"
    assert detail is None


def test_host_metrics_cache_probe_registered_in_default_probes() -> None:
    names = {p.name for p in r.get_default_probes()}
    assert "host_metrics_cache" in names


def test_startup_warns_on_stale_host_metrics_cache(caplog: pytest.LogCaptureFixture) -> None:
    with patch.object(hmc, "read_cache", return_value=hmc.CacheReadResult("stale", {}, 120.0, Path("/x"))):
        with patch.object(hmc, "dashboard_service_present", return_value=True):
            hmc.warn_host_metrics_cache_at_startup()
    assert any("host_metrics" in rec.message.lower() for rec in caplog.records)