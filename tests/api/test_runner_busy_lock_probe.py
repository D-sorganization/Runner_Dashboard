"""Tests for RunnerBusyLockProbe (issue #1987).

Verifies:
- Probe returns "ok" when lock directory is absent or empty.
- Probe returns "ok" when locks are fresh (<= 10m).
- worker_pid rule: live Worker ok at any age; dead or reused worker_pid degraded.
- Legacy locks (no worker_pid) are judged by age only, never by the dead hook pid=.
- Probe respects cache TTL.
- Probe is included in get_default_probes().
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

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


WORKER_CMDLINE = b"/home/u/actions-runners/runner-1/bin/Runner.Worker\x00spawnclient\x00"


def _fake_proc(root: Path, procs: dict[int, bytes]) -> Path:
    proc = root / "proc"
    proc.mkdir()
    for pid, cmdline in procs.items():
        (proc / str(pid)).mkdir()
        (proc / str(pid) / "cmdline").write_bytes(cmdline)
    return proc


def _old_lock(lock_dir: Path, body: str) -> Path:
    lock_dir.mkdir(exist_ok=True)
    lock_file = lock_dir / "d-sorg-local-Desktop-1.lock"
    lock_file.write_text(body, encoding="utf-8")
    old_time = time.time() - 1200  # 20 minutes ago
    os.utime(lock_file, (old_time, old_time))
    return lock_file


# Issue #1987: same four cases as the cleanup/autoscaler rule. A live
# worker_pid is healthy at any age; a dead or reused one is stale; a legacy
# lock (no worker_pid) is judged by age alone, never by its dead hook pid=.
_PROBE_CASES = {
    "live_worker_ok": ("pid=11\nworker_pid=100\n", {100: WORKER_CMDLINE}, "ok"),
    "dead_worker_pid_degraded": ("pid=11\nworker_pid=100\n", {}, "degraded"),
    "reused_pid_degraded": ("pid=11\nworker_pid=100\n", {100: b"/usr/bin/python3\x00"}, "degraded"),
    "legacy_old_lock_degraded_by_age": ("pid=11\n", {}, "degraded"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_PROBE_CASES))
async def test_busy_lock_probe_worker_pid_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    body, procs, expected = _PROBE_CASES[case]
    lock_dir = tmp_path / "runner-busy"
    _old_lock(lock_dir, body)
    monkeypatch.setenv("PROC_ROOT", str(_fake_proc(tmp_path, procs)))

    probe = r.RunnerBusyLockProbe(lock_dir=lock_dir, stale_threshold_seconds=600, cache_ttl_seconds=0)
    status, detail = await probe.check()

    assert status == expected
    if expected == "degraded":
        assert detail is not None and "d-sorg-local-Desktop-1.lock" in detail
    else:
        assert detail is None


@pytest.mark.asyncio
async def test_busy_lock_probe_ok_when_legacy_lock_is_fresh_even_if_hook_pid_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    (lock_dir / "r.lock").write_text("pid=11\n", encoding="utf-8")
    monkeypatch.setenv("PROC_ROOT", str(_fake_proc(tmp_path, {})))

    probe = r.RunnerBusyLockProbe(lock_dir=lock_dir, stale_threshold_seconds=600, cache_ttl_seconds=0)
    assert await probe.check() == ("ok", None)


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
