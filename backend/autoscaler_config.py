"""Configuration constants and environment helpers for the runner auto-scaler.

All threshold constants and env-var resolution live here so that the other
autoscaler modules can import a single, stable source of truth.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import TextIO

try:
    import psutil
except ImportError:  # psutil is optional at import time; raise on use
    psutil = None  # type: ignore[assignment]

try:
    from dashboard_config.timeouts import HttpTimeout, ResourceThreshold
except ImportError:  # When deployed standalone the package may not be on path.
    HttpTimeout = None  # type: ignore[assignment,misc]
    ResourceThreshold = None  # type: ignore[assignment,misc]


def _env_float(name: str, default: float) -> float:
    """Read an environment variable as float, returning *default* on missing/invalid."""
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    """Read an environment variable as int, returning *default* on missing/invalid."""
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


_DEFAULT_CPU_HIGH = ResourceThreshold.DISK_WARN_PERCENT if ResourceThreshold else 85.0
_DEFAULT_MEM_HIGH = ResourceThreshold.DISK_WARN_PERCENT if ResourceThreshold else 85.0
_DEFAULT_DISK_HIGH = ResourceThreshold.DISK_CRITICAL_PERCENT if ResourceThreshold else 92.0
_DEFAULT_DISK_MIN_FREE_GB = ResourceThreshold.DISK_MIN_FREE_GB if ResourceThreshold else 25.0

CPU_HIGH = _env_float("AUTOSCALER_CPU_HIGH", _DEFAULT_CPU_HIGH)
CPU_LOW = _env_float("AUTOSCALER_CPU_LOW", 40.0)
MEM_HIGH = _env_float("AUTOSCALER_MEM_HIGH", _DEFAULT_MEM_HIGH)
DISK_HIGH = _env_float("AUTOSCALER_DISK_HIGH", _DEFAULT_DISK_HIGH)
DISK_MIN_FREE_GB = _env_float("AUTOSCALER_DISK_MIN_FREE_GB", _DEFAULT_DISK_MIN_FREE_GB)
# 2.5 is intentionally generous: 16 parallel runners each running pip-install
# steps can briefly spike load without indicating true saturation. The old
# default of 1.5 fired constantly in that scenario.  Override via env var.
LOAD_PER_CORE = _env_float("AUTOSCALER_LOAD_PER_CORE", 2.5)
SUSTAIN_SECS = _env_int("AUTOSCALER_SUSTAIN_SECS", 120)
POLL_SECONDS = _env_int("AUTOSCALER_POLL_SECONDS", 15)
MIN_ONLINE = _env_int("AUTOSCALER_MIN_ONLINE", 1)
MAX_STEP = _env_int("AUTOSCALER_MAX_SCALE_STEP", 1)
DRY_RUN = bool(_env_int("AUTOSCALER_DRY_RUN", 0))
RUNNER_SCHEDULER_BIN = os.environ.get("RUNNER_SCHEDULER_BIN", "/usr/local/bin/runner-scheduler")
RUNNER_SCHEDULE_CONFIG = os.path.expanduser(
    os.environ.get(
        "RUNNER_SCHEDULE_CONFIG",
        "~/.config/runner-dashboard/runner-schedule.json",
    )
)
RUNNER_BASE_DIR = os.path.expanduser(os.environ.get("RUNNER_BASE_DIR", "~/actions-runners"))

# Issue #651: layered busy detection. The Runner.Worker child of MainPID is the
# primary signal, but there is a 1-2s race window between job pickup (Listener
# has accepted a job, written `_work/_temp/_runner_file_commands/`) and Worker
# fork. During that window we'd otherwise misread the unit as idle and kill it,
# leaving residue that breaks the next allocation. Two complementary signals
# close the window:
#
#   1. Pickup-directory mtime check (autoscaler_busy): the Listener creates
#      files under `_work/_temp/_runner_file_commands/` BEFORE forking the
#      Worker. A recent mtime means "Listener has accepted a job, may or may
#      not have Worker yet — treat as busy".
#   2. Job-pickup hook lockfile (deploy/runner-hooks/job-started.sh): the
#      Worker writes a sentinel lockfile when it starts. This catches the
#      inverse race — Worker is alive but psutil's process tree walk missed
#      it (transient /proc race, child reparented, etc.).
#
# The pickup-directory check fires at the moment of job acceptance; the
# lockfile fires once the Worker is alive. Together they cover the full
# pre-Worker → Worker-running lifecycle without relying on log scraping.
RUNNER_BUSY_LOCK_DIR = Path(os.environ.get("RUNNER_BUSY_LOCK_DIR", "/var/run/runner-busy"))
RUNNER_BUSY_LOCK_MAX_AGE_SECONDS = _env_int(
    "RUNNER_BUSY_LOCK_MAX_AGE_SECONDS", 24 * 60 * 60
)  # 24h — stale lockfiles (Worker killed mid-job, never wrote completion hook)
# are GC'd by deploy/runner-cleanup.sh; the autoscaler treats them as "stale,
# not busy" to avoid permanently locking out a runner.
RUNNER_PICKUP_DIR_MAX_AGE_SECONDS = _env_int(
    "RUNNER_PICKUP_DIR_MAX_AGE_SECONDS", 30
)  # Listener handoff to Worker should be sub-second; 30s headroom is generous.
# Anything older than this is residue, not in-progress pickup.

HOSTNAME = platform.node()

_SYSTEMCTL_TIMEOUT_S = HttpTimeout.SYSTEMCTL_S if HttpTimeout else 5

# Reused by autoscaler_busy to detect lock files
_lock_fd: TextIO | None = None
