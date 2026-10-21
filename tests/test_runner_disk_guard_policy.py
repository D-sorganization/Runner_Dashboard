"""Tests for backend.runner_disk_guard_policy (RD-OPS-017 config loader)."""

from __future__ import annotations

from runner_disk_guard_policy import clear_runner_disk_guard_policy_cache, load_runner_disk_guard_policy


def test_load_runner_disk_guard_policy_reads_vendored_yaml() -> None:
    clear_runner_disk_guard_policy_cache()
    cfg = load_runner_disk_guard_policy()
    assert cfg.schema == "runner-disk-guard/v1"
    assert cfg.enabled is True
    assert cfg.trip.min_free_gb == 25.0
    assert cfg.trip.max_used_percent == 92.0
    assert cfg.fleet.multi_host_cordon_alert_min == 2
