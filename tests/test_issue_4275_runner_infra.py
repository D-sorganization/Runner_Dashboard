"""Regression tests for #4275 — runner infra cordon, leases, autoscaler."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import identity as id_mod
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import runner_lease as rl
from disk_full_cordon import CordonActionKind, CordonPhase, DiskFullCordonState, RunnerSnapshot, plan_cordon_tick
from disk_full_cordon_runtime import DEFAULT_CORDON_CLEANUP_TIMEOUT_S, process_disk_full_cordon_tick
from host_volume import probe_host_volume
from runner_disk_guard_policy import load_runner_disk_guard_policy


def test_probe_failure_is_unknown_not_zero_gb() -> None:
    def fail(_path: str) -> None:
        raise OSError("volume missing")

    result = probe_host_volume(drive="Z:", disk_usage_fn=fail)
    assert result["status"] == "unknown"
    assert result["free_gb"] is None


def test_cordon_phase_and_force_action_kinds() -> None:
    config = load_runner_disk_guard_policy()
    state = DiskFullCordonState(phase=CordonPhase.tripping, tripped_at=0.0)
    runners = [RunnerSnapshot(unit="actions.runner.x.service", active=True, busy=True)]
    result = plan_cordon_tick(
        state,
        free_gb=1.0,
        used_percent=99.0,
        config=config,
        runners=runners,
        now=config.drain.timeout_seconds + 1,
    )
    force = [a for a in result.actions if a.kind == CordonActionKind.STOP_RUNNER_FORCE]
    assert force and force[0].force is True


def test_cordon_skips_trip_when_probe_unknown(tmp_path: Path) -> None:
    state_path = tmp_path / "cordon.json"
    with patch("disk_full_cordon_runtime.probe_host_volume") as probe:
        probe.return_value = {"status": "unknown", "free_gb": None, "percent": None}
        skipped = process_disk_full_cordon_tick(
            ["actions.runner.test.service"],
            unit_is_active=lambda u: True,
            runner_is_busy=lambda u: False,
            stop_unit=lambda *a, **k: None,
            dry_run=True,
            now=time.time(),
            state_path=state_path,
        )
    assert skipped is False


def test_cleanup_timeout_below_watchdog_budget() -> None:
    assert DEFAULT_CORDON_CLEANUP_TIMEOUT_S < 120


def test_lease_sidecar_lock_preserves_concurrent_updates(tmp_path: Path) -> None:
    if not hasattr(__import__("fcntl"), "flock"):
        pytest.skip("fcntl not available")

    def acquire(tag: int) -> None:
        mgr = rl.LeaseManager(config_dir=tmp_path)
        principal = id_mod.Principal(
            id=f"p{tag}",
            type="bot",
            name="t",
            roles=["operator"],
            quotas=id_mod.Quota(max_runners=99),
        )
        mgr.acquire_lease(principal, f"runner-{tag}")

    with (
        patch("runner_lease.validate_config_path"),
        patch("security.validate_config_path", side_effect=lambda p, *a, **k: p),
    ):
        rl.LeaseManager(config_dir=tmp_path).save_leases()
        threads = [threading.Thread(target=acquire, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        mgr = rl.LeaseManager(config_dir=tmp_path)
        mgr.load_leases()
        assert len(mgr.leases) == 4
