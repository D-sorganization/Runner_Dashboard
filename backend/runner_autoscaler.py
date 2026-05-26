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

# systemd watchdog / ready notification (A1).
# Importing inside a try/except keeps the autoscaler runnable outside
# systemd (dev hosts, CI, WSL) without a hard dependency.
try:
    from systemd.daemon import notify as _sd_notify
except ImportError:
    _sd_notify = None  # type: ignore[assignment]

# Re-export sub-module symbols so that callers (and existing tests) that do
#   import runner_autoscaler as ra; ra._env_float(...); ra.DRY_RUN; ...
# continue to work without modification.
from autoscaler_busy import (
    _runner_busy_via_lockfile,
    _runner_busy_via_pickup_dir,
    _runner_is_busy,
)
from autoscaler_config import (
    ACTION_COOLDOWN_SECONDS,
    CPU_HIGH,
    CPU_LOW,
    DISK_HIGH,
    DISK_MIN_FREE_GB,
    DRY_RUN,  # noqa: PLC0414 (explicit re-export for tests)
    HOSTNAME,
    IO_PRESSURE_FULL_HIGH,
    IO_PRESSURE_FULL_LOW,
    LOAD_PER_CORE,
    MAX_STEP,
    MEM_HIGH,
    MIN_ONLINE,
    POLL_SECONDS,
    RECOVERY_MIN_ONLINE,
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
    _io_pressure_snapshot,
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
    "ACTION_COOLDOWN_SECONDS",
    "IO_PRESSURE_FULL_HIGH",
    "IO_PRESSURE_FULL_LOW",
    "LOAD_PER_CORE",
    "MAX_STEP",
    "MEM_HIGH",
    "MIN_ONLINE",
    "Path",
    "POLL_SECONDS",
    "RECOVERY_MIN_ONLINE",
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
    "_io_pressure_snapshot",
    "_sample",
    "_scheduled_desired_count",
]


def _surplus_runner_count(active_count: int, scheduled_desired: int) -> int:
    """Return how many active runners exceed schedule without breaching the floor."""

    protected_target = max(MIN_ONLINE, scheduled_desired)
    return max(0, active_count - protected_target)


def _recovery_floor_target(scheduled_desired: int) -> int:
    """Return the small pool restored after overload clears but before full recovery."""

    scheduled_target = max(0, scheduled_desired)
    if scheduled_target == 0:
        return MIN_ONLINE
    return max(MIN_ONLINE, min(RECOVERY_MIN_ONLINE, scheduled_target))


def _should_restore_recovery_floor(*, overloaded: bool, active_count: int, scheduled_desired: int) -> bool:
    """Return whether the autoscaler should rebuild the minimum working pool."""

    return (
        not overloaded and active_count < _recovery_floor_target(scheduled_desired) and active_count < scheduled_desired
    )


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


def _send_watchdog() -> None:
    """Reset the systemd watchdog (A1).

    Called once per poll iteration. Outside systemd (``_sd_notify is None``
    or ``WATCHDOG_USEC`` unset) this is a no-op so the autoscaler runs
    locally without modification. A failing notifier (rare, but possible
    during shutdown) is swallowed — the next tick will retry.

    Pre-condition: none. Post-condition: never raises.
    """
    if _sd_notify is None:
        return
    if not os.environ.get("WATCHDOG_USEC", "").strip():
        return
    try:
        _sd_notify("WATCHDOG=1")
    except Exception:  # noqa: BLE001
        log.exception("autoscaler watchdog notification failed; will retry next tick")


def _run_poll_loop() -> None:
    """Infinite poll loop: sample resources, classify runners, scale up/down."""
    history_len = max(3, SUSTAIN_SECS // max(POLL_SECONDS, 1))
    samples: deque[tuple[float, float, float, float, float]] = deque(maxlen=history_len)
    last_scale_action_ts = 0.0

    # A1: notify systemd we're up. Pairs with the periodic _send_watchdog()
    # call inside the loop body below.
    if _sd_notify is not None:
        try:
            watchdog_usec = os.environ.get("WATCHDOG_USEC", "120000000")
            _sd_notify(f"READY=1\nWATCHDOG_USEC={watchdog_usec}")
        except Exception:  # noqa: BLE001
            log.exception("autoscaler READY notification failed; continuing")

    while True:
        _send_watchdog()
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
            io_pressure = _io_pressure_snapshot() or {}
            io_full_avg10 = float(io_pressure.get("full_avg10", 0.0))

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
                or io_full_avg10 >= IO_PRESSURE_FULL_HIGH
            )
            recovered = (
                avg_cpu <= CPU_LOW
                and avg_mem < MEM_HIGH - 10
                and avg_load < LOAD_PER_CORE * 0.7
                and avg_disk < DISK_HIGH - 5
                and min_disk_free > DISK_MIN_FREE_GB
                and io_full_avg10 <= IO_PRESSURE_FULL_LOW
            )

            scheduled_desired = _scheduled_desired_count(len(units))
            surplus = _surplus_runner_count(len(active), scheduled_desired)
            recovery_floor_target = _recovery_floor_target(scheduled_desired)

            log.info(
                "sample cpu=%.1f%% mem=%.1f%% load/core=%.2f disk=%.1f%% free=%.1fGB io_full10=%.1f%%"
                " active=%d busy=%d idle=%d inactive=%d scheduled=%d recovery_floor=%d min_online=%d",
                avg_cpu,
                avg_mem,
                avg_load,
                avg_disk,
                min_disk_free,
                io_full_avg10,
                len(active),
                len(busy),
                len(idle_active),
                len(inactive),
                scheduled_desired,
                recovery_floor_target,
                MIN_ONLINE,
            )

            now = time.monotonic()
            cooldown_remaining = max(0.0, ACTION_COOLDOWN_SECONDS - (now - last_scale_action_ts))
            if cooldown_remaining > 0:
                log.info("scale action cooldown active for %.0fs; holding runner state", cooldown_remaining)
            elif surplus and idle_active:
                to_stop = idle_active[: min(MAX_STEP, surplus)]
                acted = False
                for u in to_stop:
                    acted = _stop_unit(u) or acted
                if acted:
                    last_scale_action_ts = now
            elif overloaded and len(active) > MIN_ONLINE:
                room = len(active) - MIN_ONLINE
                to_stop = idle_active[: min(MAX_STEP, room)]
                if not to_stop and busy:
                    log.info(
                        "overloaded but all %d active runners busy — not interrupting jobs",
                        len(busy),
                    )
                acted = False
                for u in to_stop:
                    acted = _stop_unit(u) or acted
                if acted:
                    last_scale_action_ts = now
            elif (
                _should_restore_recovery_floor(
                    overloaded=overloaded,
                    active_count=len(active),
                    scheduled_desired=scheduled_desired,
                )
                and inactive
            ):
                room = max(0, recovery_floor_target - len(active))
                to_start = inactive[: min(MAX_STEP, room)]
                acted = False
                log.info(
                    "host no longer overloaded; restoring recovery floor target=%d",
                    recovery_floor_target,
                )
                for u in to_start:
                    acted = _start_unit(u) or acted
                if acted:
                    last_scale_action_ts = now
            elif recovered and inactive and len(active) < scheduled_desired:
                room = max(0, scheduled_desired - len(active))
                to_start = inactive[: min(MAX_STEP, room)]
                acted = False
                for u in to_start:
                    acted = _start_unit(u) or acted
                if acted:
                    last_scale_action_ts = now

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
        "poll=%ss min_online=%s recovery_min_online=%s step=%s "
        "io_full_high=%s io_full_low=%s action_cooldown=%ss dry=%s",
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
        RECOVERY_MIN_ONLINE,
        MAX_STEP,
        IO_PRESSURE_FULL_HIGH,
        IO_PRESSURE_FULL_LOW,
        ACTION_COOLDOWN_SECONDS,
        DRY_RUN,
    )
    _run_poll_loop()


if __name__ == "__main__":
    main()
