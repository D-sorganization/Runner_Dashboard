"""Resource sampling and scheduler integration for the runner auto-scaler.

Provides:
  - _sample()                  — one-shot CPU / memory / load / disk measurement
  - _scheduled_desired_count() — integrate with the runner-scheduler binary
  - _leased_runners()          — read active runner leases from config/leases.yml
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from autoscaler_config import (
    RUNNER_BASE_DIR,
    RUNNER_SCHEDULE_CONFIG,
    RUNNER_SCHEDULER_BIN,
    psutil,
)

log = logging.getLogger("runner-autoscaler")
_DEFAULT_RUNNER_SCHEDULER_BIN = RUNNER_SCHEDULER_BIN
_DEFAULT_PSUTIL = psutil


def _runner_scheduler_bin() -> str:
    if RUNNER_SCHEDULER_BIN != _DEFAULT_RUNNER_SCHEDULER_BIN:
        return RUNNER_SCHEDULER_BIN
    runner_autoscaler = sys.modules.get("runner_autoscaler")
    if runner_autoscaler is not None:
        return str(getattr(runner_autoscaler, "RUNNER_SCHEDULER_BIN", RUNNER_SCHEDULER_BIN))
    return RUNNER_SCHEDULER_BIN


def _psutil_dep() -> Any:
    if psutil is not _DEFAULT_PSUTIL:
        return psutil
    runner_autoscaler = sys.modules.get("runner_autoscaler")
    if runner_autoscaler is not None:
        return getattr(runner_autoscaler, "psutil", psutil)
    return psutil


def _leased_runners() -> set[str]:
    """Read config/leases.yml and return a set of leased runner_ids."""
    # Assuming config is relative to the dashboard root.
    # The autoscaler might run from a different CWD, so we should resolve this.
    path = Path(__file__).resolve().parent.parent / "config" / "leases.yml"
    if not path.exists():
        return set()
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        if not data or "leases" not in data:
            return set()
        now = time.time()
        return {
            str(lease_rec["runner_id"])
            for lease_rec in data["leases"]
            if lease_rec.get("expires_at") is None or float(lease_rec["expires_at"]) > now
        }
    except Exception as exc:
        log.warning("Failed to read leases: %s", exc)
        return set()


def _scheduled_desired_count(default: int) -> int:
    """Read the schedule service's current desired capacity when installed."""
    scheduler_bin = _runner_scheduler_bin()
    if not os.path.exists(scheduler_bin):
        return default
    try:
        env = os.environ.copy()
        env["RUNNER_SCHEDULE_CONFIG"] = RUNNER_SCHEDULE_CONFIG
        result = subprocess.run(
            [scheduler_bin, "--dry-run", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("scheduler desired lookup failed: %s", exc)
        return default
    if result.returncode != 0:
        log.debug("scheduler desired lookup failed: %s", result.stderr.strip()[:200])
        return default
    try:
        state = json.loads(result.stdout)
        return max(0, int(state.get("desired", default)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _sample() -> tuple[float, float, float, float, float]:
    """Return (cpu_percent, mem_percent, load_per_core, disk_percent, disk_free_gb)."""
    psutil_mod = _psutil_dep()
    if psutil_mod is None:
        raise RuntimeError("psutil is required for runner-autoscaler")
    cpu = psutil_mod.cpu_percent(interval=1.0)
    mem = psutil_mod.virtual_memory().percent
    try:
        load1 = os.getloadavg()[0]  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        load1 = 0.0
    cores = psutil_mod.cpu_count(logical=True) or 1
    disk_path = RUNNER_BASE_DIR if os.path.exists(RUNNER_BASE_DIR) else "/"
    usage = shutil.disk_usage(disk_path)
    disk_percent = usage.used / usage.total * 100
    disk_free_gb = usage.free / (1024**3)
    return cpu, mem, load1 / cores, disk_percent, disk_free_gb
