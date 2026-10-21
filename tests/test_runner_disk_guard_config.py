"""RM-OPS-017 runner-disk-guard/v1 config (vendored validator)."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared_scripts.runner_disk_guard_config import (
    SCHEMA_ID,
    load_runner_disk_guard_config,
    parse_runner_disk_guard_document,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repo_default_yaml_matches_rm_ops_017_thresholds() -> None:
    cfg = load_runner_disk_guard_config(_REPO_ROOT / "config" / "runner_disk_guard.yaml")
    assert cfg.schema == SCHEMA_ID
    assert cfg.trip.min_free_gb == 25.0
    assert cfg.trip.max_used_percent == 92.0
    assert cfg.drain.finish_running_jobs is True
    assert cfg.drain.timeout_seconds == 7200.0
    assert cfg.readmit.min_free_gb == 40.0
    assert cfg.readmit.max_used_percent == 85.0
    assert cfg.fleet.multi_host_cordon_alert_min == 2


def test_parse_rejects_invalid_hysteresis() -> None:
    with pytest.raises(ValueError, match="readmit.min_free_gb"):
        parse_runner_disk_guard_document(
            {
                "schema": SCHEMA_ID,
                "trip": {"min_free_gb": 40, "max_used_percent": 92},
                "drain": {"finish_running_jobs": True, "timeout_seconds": 7200},
                "readmit": {"min_free_gb": 25, "max_used_percent": 85},
                "fleet": {"multi_host_cordon_alert_min": 2},
            }
        )
