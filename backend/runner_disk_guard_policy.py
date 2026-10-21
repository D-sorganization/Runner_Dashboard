"""Runner disk guard policy loader (RD-OPS-017).

Runtime code loads RM-OPS-017 exclusively through the vendored
``shared_scripts.runner_disk_guard_config`` validator so thresholds are not
redefined in the dashboard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from shared_scripts.runner_disk_guard_config import (
    RunnerDiskGuardConfig,
    load_runner_disk_guard_config,
    resolve_runner_disk_guard_config_path,
)

log = logging.getLogger("dashboard.runner_disk_guard_policy")


class RunnerDiskGuardPolicy(Protocol):
    """Narrow view consumed by the disk-full cordon state machine."""

    enabled: bool
    trip_min_free_gb: float
    trip_max_used_percent: float
    readmit_min_free_gb: float
    readmit_max_used_percent: float
    drain_timeout_seconds: float
    finish_running_jobs: bool
    multi_host_cordon_alert_min: int


@dataclass(frozen=True, slots=True)
class _PolicyFromRm:
    enabled: bool
    trip_min_free_gb: float
    trip_max_used_percent: float
    readmit_min_free_gb: float
    readmit_max_used_percent: float
    drain_timeout_seconds: float
    finish_running_jobs: bool
    multi_host_cordon_alert_min: int

    @classmethod
    def from_rm(cls, cfg: RunnerDiskGuardConfig) -> _PolicyFromRm:
        return cls(
            enabled=cfg.enabled,
            trip_min_free_gb=cfg.trip.min_free_gb,
            trip_max_used_percent=cfg.trip.max_used_percent,
            readmit_min_free_gb=cfg.readmit.min_free_gb,
            readmit_max_used_percent=cfg.readmit.max_used_percent,
            drain_timeout_seconds=cfg.drain.timeout_seconds,
            finish_running_jobs=cfg.drain.finish_running_jobs,
            multi_host_cordon_alert_min=cfg.fleet.multi_host_cordon_alert_min,
        )


def policy_from_config(cfg: RunnerDiskGuardConfig) -> RunnerDiskGuardPolicy:
    return _PolicyFromRm.from_rm(cfg)


@lru_cache(maxsize=1)
def load_runner_disk_guard_policy(path: str | None = None) -> RunnerDiskGuardPolicy:
    """Load validated policy (cached per process)."""
    config_path = Path(path) if path else resolve_runner_disk_guard_config_path()
    try:
        cfg = load_runner_disk_guard_config(config_path)
    except FileNotFoundError:
        log.warning("runner disk guard config missing at %s; cordon disabled", config_path)
        return _PolicyFromRm(
            enabled=False,
            trip_min_free_gb=1.0,
            trip_max_used_percent=101.0,
            readmit_min_free_gb=0.0,
            readmit_max_used_percent=100.0,
            drain_timeout_seconds=7200.0,
            finish_running_jobs=True,
            multi_host_cordon_alert_min=2,
        )
    return _PolicyFromRm.from_rm(cfg)


def clear_runner_disk_guard_policy_cache() -> None:
    load_runner_disk_guard_policy.cache_clear()
