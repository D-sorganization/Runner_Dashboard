"""Tests for dispatch backpressure health gate (issue #709)."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from quick_dispatch import HealthGate, QuickDispatchRequest


@pytest.mark.asyncio
async def test_health_gate_returns_ok_when_healthy():
    gate = HealthGate(cache_ttl=5.0)
    gate._last_ok = True
    gate._last_check = asyncio.get_event_loop().time()  # fresh
    ok, reason = await gate.is_ready()
    assert ok is True


@pytest.mark.asyncio
async def test_health_gate_cache_prevents_multiple_checks():
    check_count = 0

    async def mock_aggregate(probes):
        nonlocal check_count
        check_count += 1
        return 200, {}

    gate = HealthGate(cache_ttl=60.0)
    # force cache miss
    gate._last_check = 0.0

    import quick_dispatch as qd

    original = None
    try:
        import readiness

        original = readiness.aggregate
        readiness.aggregate = mock_aggregate

        # Multiple calls should only hit the check once
        for _ in range(5):
            await gate.is_ready()

        assert check_count <= 1, f"Expected at most 1 check, got {check_count}"
    finally:
        if original:
            readiness.aggregate = original


@pytest.mark.asyncio
async def test_health_gate_returns_not_ready_on_503():
    gate = HealthGate(cache_ttl=0.001)  # tiny TTL so cache expires quickly

    async def mock_aggregate(probes):
        return 503, {"status": "down"}

    import readiness

    original = readiness.aggregate
    try:
        readiness.aggregate = mock_aggregate
        gate._last_check = 0.0  # force cache miss
        ok, reason = await gate.is_ready()
        assert ok is False
    finally:
        readiness.aggregate = original


@pytest.mark.asyncio
async def test_health_gate_invalid_cache_ttl():
    """HealthGate must reject non-positive cache_ttl."""
    with pytest.raises(AssertionError):
        HealthGate(cache_ttl=-1.0)  # negative is invalid


@pytest.mark.asyncio
async def test_health_gate_fails_open_on_exception():
    """When aggregate() raises, gate should fail open (return ok=True)."""
    gate = HealthGate(cache_ttl=0.001)  # tiny cache ttl

    async def exploding_aggregate(probes):
        raise RuntimeError("network error")

    import readiness

    original = readiness.aggregate
    try:
        readiness.aggregate = exploding_aggregate
        gate._last_check = 0.0
        ok, reason = await gate.is_ready()
        # fail open policy: ok=True when probe errors
        assert ok is True
    finally:
        readiness.aggregate = original


def test_quick_dispatch_request_has_force_field():
    """QuickDispatchRequest must have a force: bool = False field."""
    req = QuickDispatchRequest(
        repository="my-org/my-repo",
        prompt="do something helpful please",
    )
    assert hasattr(req, "force")
    assert req.force is False


def test_quick_dispatch_request_force_true():
    req = QuickDispatchRequest(
        repository="my-org/my-repo",
        prompt="do something helpful please",
        force=True,
    )
    assert req.force is True
