"""RM-OPS-017 runner-disk-guard/v1 config (vendored validator)."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared_scripts.runner_disk_guard_config import (
    SCHEMA_ID,
    RunnerDiskGuardConfigError,
    load_runner_disk_guard_config,
    validate_runner_disk_guard,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repo_default_yaml_matches_rm_ops_017_thresholds() -> None:
    cfg = load_runner_disk_guard_config(_REPO_ROOT / "config" / "runner_disk_guard.yaml")
    assert cfg.schema == SCHEMA_ID
    assert cfg.version == 1
    assert cfg.thresholds.trip_min_free_gb == 25.0
    assert cfg.thresholds.trip_used_percent == 92.0
    assert cfg.drain.finish_running_jobs is True
    assert cfg.drain.timeout_seconds == 7200
    assert cfg.thresholds.readmit_min_free_gb == 40.0
    assert cfg.thresholds.readmit_used_percent == 85.0
    assert cfg.alerts.cordoned_hosts_warning_threshold == 2
    assert cfg.cordon.min_online_after_drain == 0
    assert len(cfg.cleanup) == 2


def test_validate_rejects_invalid_hysteresis() -> None:
    with pytest.raises(RunnerDiskGuardConfigError, match="readmit_min_free_gb"):
        validate_runner_disk_guard(
            {
                "version": 1,
                "schema": SCHEMA_ID,
                "updated_at": "2026-10-21",
                "thresholds": {
                    "trip_min_free_gb": 40.0,
                    "readmit_min_free_gb": 25.0,
                    "trip_used_percent": 92.0,
                    "readmit_used_percent": 85.0,
                },
                "drain": {"timeout_seconds": 7200, "finish_running_jobs": True},
                "cordon": {"min_online_after_drain": 0, "stop_accepting_jobs_on_trip": True},
                "cleanup": {
                    "commands": [
                        {"argv": ["/usr/local/bin/runner-cleanup"], "description": "test"},
                    ]
                },
                "alerts": {"cordoned_hosts_warning_threshold": 2},
            }
        )
