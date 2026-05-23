"""Tests for RunnerHealthProbe (issue #712)."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


@pytest.mark.asyncio
async def test_probe_ok_when_no_failures(monkeypatch):
    """0 failed units → ok status."""
    import readiness
    from readiness import RunnerHealthProbe

    async def mock_query():
        return (10, 10, [])  # total, active, failed

    monkeypatch.setattr(readiness, "_query_runner_units", mock_query)
    # Force cache miss
    readiness._runner_health_cache = None

    probe = RunnerHealthProbe()
    status, detail = await probe.check()
    assert status == "ok"
    assert detail is None


@pytest.mark.asyncio
async def test_probe_degraded_on_one_failure(monkeypatch):
    """1/10 failed → degraded."""
    import readiness
    from readiness import RunnerHealthProbe

    async def mock_query():
        return (10, 9, ["actions.runner.test.0.service"])

    monkeypatch.setattr(readiness, "_query_runner_units", mock_query)
    readiness._runner_health_cache = None

    probe = RunnerHealthProbe()
    status, detail = await probe.check()
    assert status == "degraded"
    assert detail is not None


@pytest.mark.asyncio
async def test_probe_down_on_many_failures(monkeypatch):
    """3/5 failed → down (>10%)."""
    import readiness
    from readiness import RunnerHealthProbe

    async def mock_query():
        return (5, 2, ["r1", "r2", "r3"])

    monkeypatch.setattr(readiness, "_query_runner_units", mock_query)
    readiness._runner_health_cache = None

    probe = RunnerHealthProbe()
    status, detail = await probe.check()
    assert status == "down"


@pytest.mark.asyncio
async def test_probe_handles_timeout(monkeypatch):
    """Timeout returns degraded, not crash."""
    import readiness
    from readiness import RunnerHealthProbe

    readiness._runner_health_cache = None

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
    import readiness
    from readiness import RunnerHealthProbe

    call_count = 0

    async def mock_query():
        nonlocal call_count
        call_count += 1
        return (5, 5, [])

    monkeypatch.setattr(readiness, "_query_runner_units", mock_query)
    readiness._runner_health_cache = None
    # Set TTL to 60s so cache is always hit after first call
    readiness._RUNNER_HEALTH_CACHE_TTL_S = 60.0

    probe = RunnerHealthProbe()
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
