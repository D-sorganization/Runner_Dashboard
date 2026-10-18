"""Tests for RunnerBusyLockProbe (issue #1987).

Verifies:
- Probe returns "ok" when lock directory is absent or empty.
- Probe returns "ok" when locks are fresh (<= 10m).
- Probe returns "degraded" when locks are stale (> 10m) and no Worker is running.
- Probe returns "ok" when lock is > 10m but a Worker is actively running for that runner.
- Probe respects cache TTL.
- Probe is included in get_default_probes().
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import readiness as r  # noqa: E402


@pytest.mark.asyncio
async def test_busy_lock_probe_ok_when_no_lock_dir(tmp_path: Path) -> None:
    probe = r.RunnerBusyLockProbe(lock_dir=tmp_path / "nonexistent", cache_ttl_seconds=0)
    status, detail = await probe.check()
    assert status == "ok"
    assert detail is None


@pytest.mark.asyncio
async def test_busy_lock_probe_ok_when_empty_lock_dir(tmp_path: Path) -> None:
    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    probe = r.RunnerBusyLockProbe(lock_dir=lock_dir, cache_ttl_seconds=0)
    status, detail = await probe.check()
    assert status == "ok"
    assert detail is None


@pytest.mark.asyncio
async def test_busy_lock_probe_ok_when_locks_fresh(tmp_path: Path) -> None:
    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    lock_file = lock_dir / "runner-1.lock"
    lock_file.write_text("pid=123\nrunner=runner-1\n", encoding="utf-8")

    probe = r.RunnerBusyLockProbe(lock_dir=lock_dir, stale_threshold_seconds=600, cache_ttl_seconds=0)
    status, detail = await probe.check()
    assert status == "ok"
    assert detail is None


@pytest.mark.asyncio
async def test_busy_lock_probe_degraded_when_lock_stale_and_no_worker(tmp_path: Path) -> None:
    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    lock_file = lock_dir / "d-sorg-local-Desktop-1.lock"
    lock_file.write_text("pid=12345\nrunner=d-sorg-local-Desktop-1\n", encoding="utf-8")
    old_time = time.time() - 1200  # 20 minutes ago
    os.utime(lock_file, (old_time, old_time))

    probe = r.RunnerBusyLockProbe(lock_dir=lock_dir, stale_threshold_seconds=600, cache_ttl_seconds=0)
    with patch.object(probe, "_get_running_worker_cmdlines", return_value=[]):
        status, detail = await probe.check()

    assert status == "degraded"
    assert detail is not None
    assert "d-sorg-local-Desktop-1.lock" in detail
    assert "no active Worker" in detail


@pytest.mark.asyncio
async def test_busy_lock_probe_ok_when_lock_stale_but_worker_running(tmp_path: Path) -> None:
    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    lock_file = lock_dir / "d-sorg-local-Desktop-1.lock"
    lock_file.write_text("pid=12345\nrunner=d-sorg-local-Desktop-1\n", encoding="utf-8")
    old_time = time.time() - 1200
    os.utime(lock_file, (old_time, old_time))

    probe = r.RunnerBusyLockProbe(lock_dir=lock_dir, stale_threshold_seconds=600, cache_ttl_seconds=0)
    with patch.object(
        probe,
        "_get_running_worker_cmdlines",
        return_value=["/home/user/actions-runners/runner-1/bin/Runner.Worker d-sorg-local-Desktop-1"],
    ):
        status, detail = await probe.check()

    assert status == "ok"
    assert detail is None


@pytest.mark.asyncio
async def test_busy_lock_probe_caches_result(tmp_path: Path) -> None:
    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    probe = r.RunnerBusyLockProbe(lock_dir=lock_dir, cache_ttl_seconds=60)

    call_count = 0
    orig_check = probe._check_busy_locks

    def wrapped_check() -> tuple[r.ProbeStatus, str | None]:
        nonlocal call_count
        call_count += 1
        return orig_check()

    probe._check_busy_locks = wrapped_check  # type: ignore[method-assign]

    await probe.check()
    await probe.check()
    await probe.check()

    assert call_count == 1, f"Expected 1 check, got {call_count}"


def test_busy_lock_probe_in_default_probes() -> None:
    names = {p.name for p in r.get_default_probes()}
    assert "runner_busy_locks" in names
