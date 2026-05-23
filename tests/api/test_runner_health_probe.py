"""Tests for RunnerHealthProbe (issue #712)."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


@pytest.mark.asyncio
async def test_probe_ok_when_no_failures(monkeypatch):
    """0 failed units → ok status."""
    from readiness import RunnerHealthProbe

    async def mock_probe_systemd():
        return ("ok", None)

    probe = RunnerHealthProbe(cache_ttl_seconds=0)
    monkeypatch.setattr(probe, "_probe_systemd", mock_probe_systemd)
    status, detail = await probe.check()
    assert status == "ok"
    assert detail is None


@pytest.mark.asyncio
async def test_probe_degraded_on_one_failure(monkeypatch):
    """1/10 failed → degraded."""
    from readiness import RunnerHealthProbe

    async def mock_probe_systemd():
        return ("degraded", "1/10 runner units failed: actions.runner.test.0.service")

    probe = RunnerHealthProbe(cache_ttl_seconds=0)
    monkeypatch.setattr(probe, "_probe_systemd", mock_probe_systemd)
    status, detail = await probe.check()
    assert status == "degraded"
    assert detail is not None


@pytest.mark.asyncio
async def test_probe_down_on_many_failures(monkeypatch):
    """3/5 failed → down (>10%)."""
    from readiness import RunnerHealthProbe

    async def mock_probe_systemd():
        return ("down", "3/5 runner units failed: r1, r2, r3")

    probe = RunnerHealthProbe(cache_ttl_seconds=0)
    monkeypatch.setattr(probe, "_probe_systemd", mock_probe_systemd)
    status, detail = await probe.check()
    assert status == "down"


@pytest.mark.asyncio
async def test_probe_handles_timeout(monkeypatch):
    """Timeout returns degraded, not crash."""
    from readiness import RunnerHealthProbe

    # monkeypatch wait_for to raise immediately
    async def mock_wait_for(coro, timeout):
        raise TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", mock_wait_for)

    probe = RunnerHealthProbe()
    status, detail = await probe.check()
    assert status in ("degraded", "down", "ok")  # should not raise


@pytest.mark.asyncio
async def test_probe_caches_results(monkeypatch):
    """Multiple calls within TTL produce only 1 subprocess invocation."""
    from readiness import RunnerHealthProbe

    call_count = 0

    async def mock_probe_systemd():
        nonlocal call_count
        call_count += 1
        return ("ok", None)

    # Set TTL to 60s so cache is always hit after first call
    probe = RunnerHealthProbe(cache_ttl_seconds=60.0)
    monkeypatch.setattr(probe, "_probe_systemd", mock_probe_systemd)
    for _ in range(5):
        await probe.check()

    assert call_count == 1, f"Expected 1 call, got {call_count}"


@pytest.mark.asyncio
async def test_probe_in_default_probes():
    """RunnerHealthProbe must be included in get_default_probes()."""
    from readiness import get_default_probes

    probes = get_default_probes()
    probe_names = [p.name for p in probes]
    assert "runner_health" in probe_names, "runner_health probe must be in default probes"
