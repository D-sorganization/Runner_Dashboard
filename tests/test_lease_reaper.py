"""Tests for the background lease reaper task (A2).

The dashboard registers `_lease_reaper_loop` at startup so that crashed
runners' stale leases are removed without waiting for the next caller of
`get_active_leases()`. These tests verify the loop:

  - calls `prune_expired()` periodically,
  - increments a counter when it actually prunes,
  - survives transient `prune_expired()` failures,
  - exits cleanly on cancellation (no leaked tasks at shutdown).

The loop's sleep interval is mocked so the tests stay fast.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


@pytest.mark.asyncio
async def test_lease_reaper_calls_prune_expired_periodically(monkeypatch) -> None:
    """A2: the reaper iterates and calls prune_expired() on each tick."""
    import runner_lease
    import server

    pruner = MagicMock(return_value=0)
    monkeypatch.setattr(runner_lease.lease_manager, "prune_expired", pruner)

    # Override the interval so the test takes ms, not minutes.
    monkeypatch.setattr(server, "LEASE_REAPER_INTERVAL_S", 0.05)

    task = asyncio.create_task(server._lease_reaper_loop())
    await asyncio.sleep(0.18)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Two or three ticks should have fired within 180ms at a 50ms interval.
    assert pruner.call_count >= 2


@pytest.mark.asyncio
async def test_lease_reaper_logs_when_it_prunes(monkeypatch, caplog) -> None:
    """A2: a non-zero prune count produces exactly one INFO log line so
    `journalctl | grep lease_reaper` is meaningful."""
    import logging

    import runner_lease
    import server

    caplog.set_level(logging.INFO, logger="dashboard")
    monkeypatch.setattr(runner_lease.lease_manager, "prune_expired", MagicMock(return_value=3))
    monkeypatch.setattr(server, "LEASE_REAPER_INTERVAL_S", 0.02)

    task = asyncio.create_task(server._lease_reaper_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    log_lines = [rec.getMessage() for rec in caplog.records if "lease_reaper" in rec.getMessage()]
    assert log_lines, "expected at least one lease_reaper log line"
    assert any("3" in line for line in log_lines), f"expected the pruned count in the log; got {log_lines!r}"


@pytest.mark.asyncio
async def test_lease_reaper_survives_prune_exception(monkeypatch) -> None:
    """A2: if prune_expired() raises (corrupt YAML, transient FS error,
    etc.), the reaper must not exit the loop."""
    import runner_lease
    import server

    calls = {"n": 0}

    def flaky() -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated read failure")
        return 0

    monkeypatch.setattr(runner_lease.lease_manager, "prune_expired", flaky)
    monkeypatch.setattr(server, "LEASE_REAPER_INTERVAL_S", 0.02)

    task = asyncio.create_task(server._lease_reaper_loop())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls["n"] >= 2, "reaper must continue past a single prune failure"


@pytest.mark.asyncio
async def test_lease_reaper_interval_default_is_safe() -> None:
    """A2: the default interval must protect the lease file from lock thrash
    while still reclaiming stale leases promptly. Bound: 30s ≤ default ≤ 600s.
    """
    import server

    assert hasattr(server, "LEASE_REAPER_INTERVAL_S"), "module must export the interval constant"
    assert 30 <= server.LEASE_REAPER_INTERVAL_S <= 600
