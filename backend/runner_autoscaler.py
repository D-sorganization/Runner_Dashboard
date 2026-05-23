#!/usr/bin/env python3
"""Performance-aware runner auto-scaler — main loop and public entry point.

Monitors the local machine's CPU, memory, load, and disk, taking idle runners
offline (by stopping their systemd unit) when thresholds are exceeded, and
bringing them back online when load drops.  Only *idle* runners are stopped;
a busy runner is never interrupted mid-job.  A minimum number of runners
(``MIN_ONLINE_RUNNERS``) is always kept running so at least one lane stays
available for small jobs.

Implementation is split across focused modules:
  autoscaler_config    — env helpers and all threshold constants
  autoscaler_systemd   — unit enumeration, state inspection, start/stop
  autoscaler_busy      — layered busy-detection logic
  autoscaler_sampling  — resource sampling and scheduler integration

Runs as a separate systemd unit (``runner-autoscaler.service``) every
``POLL_INTERVAL`` seconds.  See ``deploy/runner-autoscaler.service`` for
the unit file.

Environment variables:
    AUTOSCALER_CPU_HIGH        default 85  — scale down above this % sustained
    AUTOSCALER_CPU_LOW         default 40  — scale back up below this %
    AUTOSCALER_MEM_HIGH        default 85  — memory headroom threshold
    AUTOSCALER_DISK_HIGH       default 92  — disk usage threshold
    AUTOSCALER_DISK_MIN_FREE_GB default 25 — minimum free disk headroom
    AUTOSCALER_LOAD_PER_CORE   default 2.5 — sustained load1 / cpu_count
    AUTOSCALER_SUSTAIN_SECS    default 120 — how long a threshold must hold
    AUTOSCALER_POLL_SECONDS    default 15  — sample cadence
    AUTOSCALER_MIN_ONLINE      default 1   — never reduce below this count
    AUTOSCALER_MAX_SCALE_STEP  default 1   — runners stopped/started per cycle
    AUTOSCALER_DRY_RUN         default 0   — if 1, log decisions but don't act
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections import deque

# Re-export sub-module symbols so that callers (and existing tests) that do
#   import runner_autoscaler as ra; ra._env_float(...); ra.DRY_RUN; ...
# continue to work without modification.
from autoscaler_busy import (
    _runner_busy_via_lockfile,
    _runner_busy_via_pickup_dir,
    _runner_is_busy,
)
from autoscaler_config import (
    CPU_HIGH,
    CPU_LOW,
    DISK_HIGH,
    DISK_MIN_FREE_GB,
    DRY_RUN,  # noqa: PLC0414 (explicit re-export for tests)
    HOSTNAME,
    LOAD_PER_CORE,
    MAX_STEP,
    MEM_HIGH,
    MIN_ONLINE,
    POLL_SECONDS,
    RUNNER_BUSY_LOCK_DIR,
    RUNNER_BUSY_LOCK_MAX_AGE_SECONDS,
    RUNNER_PICKUP_DIR_MAX_AGE_SECONDS,
    RUNNER_SCHEDULER_BIN,
    SUSTAIN_SECS,
    Path,  # noqa: PLC0414 (explicit re-export for tests)
    _env_float,
    _env_int,
    _lock_fd,
    psutil,
)
from autoscaler_sampling import (
    _leased_runners,
    _sample,
    _scheduled_desired_count,
)
from autoscaler_systemd import (
    _list_runner_units,
    _runner_name_for_unit,
    _runner_workdir_for_unit,
    _start_unit,
    _stop_unit,
    _unit_is_active,
    _unit_state,
)

log = logging.getLogger("runner-autoscaler")
logging.basicConfig(
    level=os.environ.get("AUTOSCALER_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)

__all__ = [
    "CPU_HIGH",
    "CPU_LOW",
    "DISK_HIGH",
    "DISK_MIN_FREE_GB",
    "DRY_RUN",
    "HOSTNAME",
    "LOAD_PER_CORE",
    "MAX_STEP",
    "MEM_HIGH",
    "MIN_ONLINE",
    "Path",
    "POLL_SECONDS",
    "RUNNER_BUSY_LOCK_DIR",
    "RUNNER_BUSY_LOCK_MAX_AGE_SECONDS",
    "RUNNER_PICKUP_DIR_MAX_AGE_SECONDS",
    "RUNNER_SCHEDULER_BIN",
    "SUSTAIN_SECS",
    "_env_float",
    "_env_int",
    "_lock_fd",
    "psutil",
    "_list_runner_units",
    "_runner_name_for_unit",
    "_runner_workdir_for_unit",
    "_start_unit",
    "_stop_unit",
    "_unit_is_active",
    "_unit_state",
    "_runner_busy_via_lockfile",
    "_runner_busy_via_pickup_dir",
    "_runner_is_busy",
    "_leased_runners",
    "_sample",
    "_scheduled_desired_count",
]


def _acquire_lock() -> None:
    """Acquire the autoscaler singleton lock file, exiting with code 75 on failure.

    Walks a candidate list; the first writable path wins. The old code
    hard-coded /var/run with a directory-existence check, which is wrong when
    the service runs as a non-root user: /run exists but isn't writable for
    the runner user, so open() failed → systemd crash-loop. See
    d-sorg-local-ControlTower post-2026-05-18 deploy.
    """
    global _lock_fd  # noqa: PLW0603
    try:
        import fcntl

        lock_path = ""
        last_err: OSError | None = None
        for candidate in (
            os.environ.get("AUTOSCALER_LOCK_PATH"),
            "/var/run/runner-autoscaler.lock",
            f"/run/user/{os.getuid()}/runner-autoscaler.lock",  # type: ignore[attr-defined]
            os.path.expanduser("~/.cache/runner-autoscaler.lock"),
            "/tmp/runner-autoscaler.lock",
        ):
            if not candidate:
                continue
            try:
                os.makedirs(os.path.dirname(candidate), exist_ok=True)
                _lock_fd = open(candidate, "w")
                fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined,name-defined]
                lock_path = candidate
                break
            except OSError as exc:
                last_err = exc
                if _lock_fd is not None:
                    try:
                        _lock_fd.close()
                    except OSError:
                        pass
                    _lock_fd = None
                continue
        if not lock_path:
            log.error(
                "Could not acquire lock on any candidate path; last error: %s",
                last_err,
            )
            sys.exit(75)
        log.info("acquired autoscaler lock at %s", lock_path)
    except ImportError:
        pass


def _run_poll_loop() -> None:
    """Infinite poll loop: sample resources, classify runners, scale up/down."""
    history_len = max(3, SUSTAIN_SECS // max(POLL_SECONDS, 1))
    samples: deque[tuple[float, float, float, float, float]] = deque(maxlen=history_len)

    while True:
        try:
            cpu, mem, load, disk, disk_free = _sample()
            samples.append((cpu, mem, load, disk, disk_free))
            if len(samples) < history_len:
                time.sleep(POLL_SECONDS)
                continue

            avg_cpu = sum(s[0] for s in samples) / len(samples)
            avg_mem = sum(s[1] for s in samples) / len(samples)
            avg_load = sum(s[2] for s in samples) / len(samples)
            avg_disk = sum(s[3] for s in samples) / len(samples)
            min_disk_free = min(s[4] for s in samples)

            units = _list_runner_units()
            if not units:
                log.info("no runner units detected; idling")
                time.sleep(POLL_SECONDS * 4)
                continue

            active = [u for u in units if _unit_is_active(u)]
            inactive = [u for u in units if u not in active]
            busy = {u for u in active if _runner_is_busy(u)}
            leased = _leased_runners()
            idle_active = [u for u in active if u not in busy and not any(r in u for r in leased)]

            if leased:
                log.info(
                    "Detected %d active leases: %s",
                    len(leased),
                    ", ".join(sorted(leased)),
                )

            overloaded = (
                avg_cpu >= CPU_HIGH
                or avg_mem >= MEM_HIGH
                or avg_load >= LOAD_PER_CORE
                or avg_disk >= DISK_HIGH
                or min_disk_free <= DISK_MIN_FREE_GB
            )
            recovered = (
                avg_cpu <= CPU_LOW
                and avg_mem < MEM_HIGH - 10
                and avg_load < LOAD_PER_CORE * 0.7
                and avg_disk < DISK_HIGH - 5
                and min_disk_free > DISK_MIN_FREE_GB
            )

            scheduled_desired = _scheduled_desired_count(len(units))
            surplus = max(0, len(active) - scheduled_desired)

            log.info(
                "sample cpu=%.1f%% mem=%.1f%% load/core=%.2f disk=%.1f%% free=%.1fGB"
                " active=%d busy=%d idle=%d inactive=%d scheduled=%d",
                avg_cpu,
                avg_mem,
                avg_load,
                avg_disk,
                min_disk_free,
                len(active),
                len(busy),
                len(idle_active),
                len(inactive),
                scheduled_desired,
            )

            if surplus and idle_active:
                to_stop = idle_active[: min(MAX_STEP, surplus)]
                for u in to_stop:
                    _stop_unit(u)
            elif overloaded and len(active) > MIN_ONLINE:
                room = len(active) - MIN_ONLINE
                to_stop = idle_active[: min(MAX_STEP, room)]
                if not to_stop and busy:
                    log.info(
                        "overloaded but all %d active runners busy — not interrupting jobs",
                        len(busy),
                    )
                for u in to_stop:
                    _stop_unit(u)
            elif recovered and inactive and len(active) < scheduled_desired:
                room = max(0, scheduled_desired - len(active))
                to_start = inactive[: min(MAX_STEP, room)]
                for u in to_start:
                    _start_unit(u)

        except Exception as exc:  # noqa: BLE001
            log.exception("autoscaler tick failed: %s", exc)
        _notify_watchdog()
        time.sleep(POLL_SECONDS)


# systemd watchdog notification (issue #707)
try:
    from systemd.daemon import notify as _sd_notify_autoscaler  # type: ignore[import-not-found,unused-ignore]
except ImportError:
    _sd_notify_autoscaler = None  # type: ignore[assignment]


def _notify_watchdog() -> None:
    """Send WATCHDOG=1 to systemd if available (issue #707).

    Pre-condition: none (safe to call unconditionally).
    Post-condition: watchdog timer is reset when sd_notify is available.
    """
    if _sd_notify_autoscaler is not None:
        try:
            _sd_notify_autoscaler("WATCHDOG=1")
        except Exception as exc:  # noqa: BLE001
            log.warning("autoscaler: sd_notify WATCHDOG=1 failed: %s", exc)


def main() -> None:
    """Entry point: acquire the singleton lock, then run the poll loop."""
    if psutil is None:
        log.error("psutil not installed; cannot run autoscaler")
        raise SystemExit(2)

    _acquire_lock()

    log.info(
        "autoscaler start host=%s cpu_high=%s cpu_low=%s mem_high=%s "
        "disk_high=%s disk_min_free_gb=%s load_per_core=%s sustain=%ss "
        "poll=%ss min_online=%s step=%s dry=%s",
        HOSTNAME,
        CPU_HIGH,
        CPU_LOW,
        MEM_HIGH,
        DISK_HIGH,
        DISK_MIN_FREE_GB,
        LOAD_PER_CORE,
        SUSTAIN_SECS,
        POLL_SECONDS,
        MIN_ONLINE,
        MAX_STEP,
        DRY_RUN,
    )
    _run_poll_loop()


if __name__ == "__main__":
    main()
