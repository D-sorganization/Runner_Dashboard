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

import json
import logging
import os
import re
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
    ACTION_COOLDOWN_SECONDS,
    COOLDOWN_SECS,
    CPU_HIGH,
    CPU_LOW,
    DISK_HIGH,
    DISK_MIN_FREE_GB,
    DRY_RUN,  # noqa: PLC0414 (explicit re-export for tests)
    FILTER_START_LABELS,
    FILTER_STOP_LABELS,
    HDD_DEFAULT,
    HDD_DEVICE,
    HDD_DISK_HIGH,
    HDD_IO_HIGH,
    HDD_LABELS,
    HDD_MAX_ONLINE,
    HDD_MIN_FREE_GB,
    HDD_MIN_ONLINE,
    HDD_PATH,
    HDD_PATTERN,
    HDD_START_ENABLED,
    HDD_STOP_ENABLED,
    HOSTNAME,
    IO_PRESSURE_FULL_HIGH,
    IO_PRESSURE_FULL_LOW,
    LOAD_PER_CORE,
    MAX_STEP,
    MEM_HIGH,
    MIN_ONLINE,
    NVME_CACHE_HIGH,
    NVME_DEFAULT,
    NVME_DISK_HIGH,
    NVME_LABELS,
    NVME_MAX_ONLINE,
    NVME_MIN_FREE_GB,
    NVME_MIN_ONLINE,
    NVME_PATH,
    # new pool config imports
    NVME_PATTERN,
    NVME_START_ENABLED,
    NVME_STOP_ENABLED,
    POLL_SECONDS,
    RECOVERY_MIN_ONLINE,
    RUNNER_BASE_DIR,
    RUNNER_BUSY_LOCK_DIR,
    RUNNER_BUSY_LOCK_MAX_AGE_SECONDS,
    RUNNER_PICKUP_DIR_MAX_AGE_SECONDS,
    RUNNER_SCHEDULE_CONFIG,
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
    _sample_pool_disk,
    _scheduled_desired_count,
    get_disk_utilization_percent,
)
from autoscaler_systemd import (
    _list_runner_units,
    _runner_name_for_unit,
    _runner_workdir_for_unit,
    _start_unit,
    _stop_unit,
    _unit_has_safe_stop_contract,
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
    "_unit_has_safe_stop_contract",
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
    protected_target = max(0, scheduled_desired)
    if protected_target == 0:
        return MIN_ONLINE
    return max(MIN_ONLINE, min(RECOVERY_MIN_ONLINE, protected_target))


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


def _classify_unit(unit: str) -> str:
    """Classify a systemd runner unit into 'nvme', 'hdd', or 'default'."""
    unit_name = _runner_name_for_unit(unit).lower()

    if NVME_PATTERN and re.search(NVME_PATTERN, unit_name, re.IGNORECASE):
        return "nvme"

    if HDD_PATTERN and re.search(HDD_PATTERN, unit_name, re.IGNORECASE):
        return "hdd"

    return "default"


def _get_pool_config(pool_name: str) -> dict:
    """Get configuration dict for the specified pool name."""
    if pool_name == "nvme":
        return {
            "min_online": NVME_MIN_ONLINE,
            "max_online": NVME_MAX_ONLINE,
            "default_online": NVME_DEFAULT,
            "labels": NVME_LABELS,
            "start_enabled": NVME_START_ENABLED,
            "stop_enabled": NVME_STOP_ENABLED,
            "path": NVME_PATH,
            "min_free_gb": NVME_MIN_FREE_GB,
            "disk_high": NVME_DISK_HIGH,
            "cache_high": NVME_CACHE_HIGH,
        }
    elif pool_name == "hdd":
        return {
            "min_online": HDD_MIN_ONLINE,
            "max_online": HDD_MAX_ONLINE,
            "default_online": HDD_DEFAULT,
            "labels": HDD_LABELS,
            "start_enabled": HDD_START_ENABLED,
            "stop_enabled": HDD_STOP_ENABLED,
            "path": HDD_PATH,
            "min_free_gb": HDD_MIN_FREE_GB,
            "disk_high": HDD_DISK_HIGH,
            "io_high": HDD_IO_HIGH,
            "device": HDD_DEVICE,
        }
    else:  # default pool
        return {
            "min_online": MIN_ONLINE,
            "max_online": 999,
            "default_online": MIN_ONLINE,
            "labels": [],
            "start_enabled": True,
            "stop_enabled": True,
            "path": RUNNER_BASE_DIR,
            "min_free_gb": DISK_MIN_FREE_GB,
            "disk_high": DISK_HIGH,
        }


def _is_start_allowed(pool_name: str) -> bool:
    """Check if starting runners is allowed for the specified pool, considering labels/filters."""
    cfg = _get_pool_config(pool_name)
    if not cfg["start_enabled"]:
        return False
    if FILTER_START_LABELS:
        pool_labels = cfg.get("labels", [])
        if not any(label in FILTER_START_LABELS for label in pool_labels):
            return False
    return True


def _is_stop_allowed(pool_name: str) -> bool:
    """Check if stopping runners is allowed for the specified pool, considering labels/filters."""
    cfg = _get_pool_config(pool_name)
    if not cfg["stop_enabled"]:
        return False
    if FILTER_STOP_LABELS:
        pool_labels = cfg.get("labels", [])
        if not any(label in FILTER_STOP_LABELS for label in pool_labels):
            return False
    return True


def _get_scheduled_pool_desired(pool_name: str, pool_units_count: int) -> int:
    """Determine the desired count for a specific pool based on active schedule, config defaults, and pool limits."""
    pool_cfg = _get_pool_config(pool_name)
    desired = pool_cfg["default_online"]

    try:
        if os.path.exists(RUNNER_SCHEDULE_CONFIG):
            with open(RUNNER_SCHEDULE_CONFIG, encoding="utf-8") as f:
                config = json.load(f)

            if config.get("enabled", True):
                from datetime import UTC, datetime, tzinfo
                from zoneinfo import ZoneInfo

                tz: tzinfo
                try:
                    tz = ZoneInfo(config.get("timezone", "America/Los_Angeles"))
                except Exception:
                    tz = UTC

                now = datetime.now(tz)

                matched_entry = None
                for entry in config.get("schedules", []):
                    try:
                        days = set(entry.get("days", []))
                        today = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
                        yesterday = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][(now.weekday() - 1) % 7]

                        start_str = entry.get("start", "00:00")
                        end_str = entry.get("end", "23:59")

                        def parse_time(t_str):
                            h, m = t_str.split(":", 1)
                            from datetime import time as dt_time

                            return dt_time(int(h), int(m))

                        start = parse_time(start_str)
                        end = parse_time(end_str)
                        current = now.time()

                        matches = False
                        if start <= end:
                            matches = today in days and start <= current < end
                        else:
                            matches = (today in days and current >= start) or (yesterday in days and current < end)

                        if matches:
                            matched_entry = entry
                            break
                    except Exception:
                        pass

                if matched_entry:
                    pool_key = f"{pool_name}_runners"
                    if pool_key in matched_entry:
                        desired = int(matched_entry[pool_key])
                    elif pool_name == "default" and "runners" in matched_entry:
                        desired = int(matched_entry["runners"])
                else:
                    pool_default_key = f"{pool_name}_default_count"
                    if pool_default_key in config:
                        desired = int(config[pool_default_key])
                    elif pool_name == "default" and "default_count" in config:
                        desired = int(config["default_count"])
    except Exception as exc:
        log.debug("Failed to read scheduled desired count for pool %s: %s", pool_name, exc)

    min_val = pool_cfg["min_online"]
    max_val = pool_cfg["max_online"]

    max_val = min(max_val, pool_units_count)

    desired = max(min_val, min(max_val, desired))
    return desired


def _sample_all_pools() -> dict[str, dict[str, float]]:
    """Sample resources for all pools.

    Returns:
        A dict mapping pool name to a dict of metric names to values.
    """
    cpu, mem, load, disk, disk_free = _sample()

    nvme_cfg = _get_pool_config("nvme")
    nvme_disk, nvme_free = _sample_pool_disk(nvme_cfg["path"])

    hdd_cfg = _get_pool_config("hdd")
    hdd_disk, hdd_free = _sample_pool_disk(hdd_cfg["path"])
    hdd_io = get_disk_utilization_percent(hdd_cfg.get("device", ""))

    return {
        "default": {
            "cpu": cpu,
            "mem": mem,
            "load": load,
            "disk": disk,
            "disk_free": disk_free,
        },
        "nvme": {
            "cache_pressure": mem,
            "disk": nvme_disk,
            "disk_free": nvme_free,
        },
        "hdd": {
            "io_pressure": hdd_io,
            "disk": hdd_disk,
            "disk_free": hdd_free,
        },
    }


# Track when each pool was last overloaded. Keys: "default", "nvme", "hdd".
pool_last_overloaded: dict[str, float] = {
    "default": 0.0,
    "nvme": 0.0,
    "hdd": 0.0,
}


def _run_poll_loop() -> None:
    """Infinite poll loop: sample resources, classify runners, scale up/down."""
    history_len = max(3, SUSTAIN_SECS // max(POLL_SECONDS, 1))

    # Store history deques per pool
    pool_samples: dict[str, deque[dict[str, float]]] = {
        "default": deque(maxlen=history_len),
        "nvme": deque(maxlen=history_len),
        "hdd": deque(maxlen=history_len),
    }

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
            current_samples = _sample_all_pools()
            for p_name, sample_val in current_samples.items():
                pool_samples[p_name].append(sample_val)

            # Wait until we have enough history to make decisions
            if len(pool_samples["default"]) < history_len:
                time.sleep(POLL_SECONDS)
                continue

            units = _list_runner_units()
            if not units:
                log.info("no runner units detected; idling")
                time.sleep(POLL_SECONDS * 4)
                continue

            # Classify units into pools
            classified_units: dict[str, list[str]] = {
                "default": [],
                "nvme": [],
                "hdd": [],
            }
            for u in units:
                cls = _classify_unit(u)
                classified_units[cls].append(u)

            leased = _leased_runners()
            if leased:
                log.info(
                    "Detected %d active leases: %s",
                    len(leased),
                    ", ".join(sorted(leased)),
                )

            # Process each pool independently
            for pool_name in ("default", "nvme", "hdd"):
                pool_units = classified_units[pool_name]
                if not pool_units:
                    continue

                pool_cfg = _get_pool_config(pool_name)
                pool_active = [u for u in pool_units if _unit_is_active(u)]
                pool_inactive = [u for u in pool_units if u not in pool_active]
                pool_busy = {u for u in pool_active if _runner_is_busy(u)}
                pool_idle_active = [u for u in pool_active if u not in pool_busy and not any(r in u for r in leased)]

                hist = pool_samples[pool_name]
                overloaded = False
                recovered = False

                if pool_name == "default":
                    avg_cpu = sum(s["cpu"] for s in hist) / len(hist)
                    avg_mem = sum(s["mem"] for s in hist) / len(hist)
                    avg_load = sum(s["load"] for s in hist) / len(hist)
                    avg_disk = sum(s["disk"] for s in hist) / len(hist)
                    min_disk_free = min(s["disk_free"] for s in hist)

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
                    log.info(
                        "pool=default cpu=%.1f%% mem=%.1f%% load/core=%.2f disk=%.1f%% free=%.1fGB"
                        " active=%d busy=%d idle=%d inactive=%d",
                        avg_cpu,
                        avg_mem,
                        avg_load,
                        avg_disk,
                        min_disk_free,
                        len(pool_active),
                        len(pool_busy),
                        len(pool_idle_active),
                        len(pool_inactive),
                    )

                elif pool_name == "nvme":
                    avg_cache = sum(s["cache_pressure"] for s in hist) / len(hist)
                    avg_disk = sum(s["disk"] for s in hist) / len(hist)
                    min_disk_free = min(s["disk_free"] for s in hist)

                    overloaded = (
                        avg_cache >= NVME_CACHE_HIGH or avg_disk >= NVME_DISK_HIGH or min_disk_free <= NVME_MIN_FREE_GB
                    )
                    recovered = (
                        avg_cache < NVME_CACHE_HIGH - 10
                        and avg_disk < NVME_DISK_HIGH - 5
                        and min_disk_free > NVME_MIN_FREE_GB
                    )
                    log.info(
                        "pool=nvme cache=%.1f%% disk=%.1f%% free=%.1fGB active=%d busy=%d idle=%d inactive=%d",
                        avg_cache,
                        avg_disk,
                        min_disk_free,
                        len(pool_active),
                        len(pool_busy),
                        len(pool_idle_active),
                        len(pool_inactive),
                    )

                elif pool_name == "hdd":
                    avg_io = sum(s["io_pressure"] for s in hist) / len(hist)
                    avg_disk = sum(s["disk"] for s in hist) / len(hist)
                    min_disk_free = min(s["disk_free"] for s in hist)

                    overloaded = avg_io >= HDD_IO_HIGH or avg_disk >= HDD_DISK_HIGH or min_disk_free <= HDD_MIN_FREE_GB
                    recovered = (
                        avg_io < HDD_IO_HIGH - 10 and avg_disk < HDD_DISK_HIGH - 5 and min_disk_free > HDD_MIN_FREE_GB
                    )
                    log.info(
                        "pool=hdd io=%.1f%% disk=%.1f%% free=%.1fGB active=%d busy=%d idle=%d inactive=%d",
                        avg_io,
                        avg_disk,
                        min_disk_free,
                        len(pool_active),
                        len(pool_busy),
                        len(pool_idle_active),
                        len(pool_inactive),
                    )

                # Update overloaded timestamp
                if overloaded:
                    pool_last_overloaded[pool_name] = time.time()

                # Get desired count
                pool_desired = _get_scheduled_pool_desired(pool_name, len(pool_units))
                surplus = max(0, len(pool_active) - pool_desired)

                # Cooldown and recovery logic
                in_cooldown = False
                in_soft_recovery = False
                if not overloaded:
                    last_overload = pool_last_overloaded[pool_name]
                    if last_overload > 0.0:
                        elapsed = time.time() - last_overload
                        if elapsed < COOLDOWN_SECS:
                            in_cooldown = True
                        elif elapsed < 2 * COOLDOWN_SECS:
                            in_soft_recovery = True

                if surplus and pool_idle_active:
                    if _is_stop_allowed(pool_name):
                        to_stop = pool_idle_active[: min(MAX_STEP, surplus)]
                        log.info(
                            "pool=%s surplus=%d, stopping %d idle active runners",
                            pool_name,
                            surplus,
                            len(to_stop),
                        )
                        for u in to_stop:
                            _stop_unit(u)
                    else:
                        log.debug("pool=%s stop action filtered/disabled", pool_name)

                elif overloaded and len(pool_active) > pool_cfg["min_online"]:
                    room = len(pool_active) - pool_cfg["min_online"]
                    if _is_stop_allowed(pool_name):
                        to_stop = pool_idle_active[: min(MAX_STEP, room)]
                        if not to_stop and pool_busy:
                            log.info(
                                "pool=%s overloaded but all %d active runners busy — not interrupting jobs",
                                pool_name,
                                len(pool_busy),
                            )
                        if to_stop:
                            log.warning(
                                "pool=%s overloaded, stopping %d runners to maintain min_online=%d",
                                pool_name,
                                len(to_stop),
                                pool_cfg["min_online"],
                            )
                            for u in to_stop:
                                _stop_unit(u)
                    else:
                        log.debug("pool=%s stop action filtered/disabled during overload", pool_name)

                elif recovered and pool_inactive and len(pool_active) < pool_desired:
                    if in_cooldown:
                        log.info(
                            "pool=%s recovered but in cooldown (elapsed < %ds) — skipping starts",
                            pool_name,
                            COOLDOWN_SECS,
                        )
                        continue

                    if _is_start_allowed(pool_name):
                        room = pool_desired - len(pool_active)
                        step = 1 if in_soft_recovery else MAX_STEP
                        to_start = pool_inactive[: min(step, room)]
                        if to_start:
                            recovery_msg = " (soft recovery rate-limit)" if in_soft_recovery else ""
                            log.info(
                                "pool=%s recovered, starting %d runners%s",
                                pool_name,
                                len(to_start),
                                recovery_msg,
                            )
                            for u in to_start:
                                _start_unit(u)
                    else:
                        log.debug("pool=%s start action filtered/disabled", pool_name)

        except Exception as exc:  # noqa: BLE001
            log.exception("autoscaler tick failed: %s", exc)
        _notify_watchdog()
        time.sleep(POLL_SECONDS)


# systemd watchdog notification (issue #707)
try:
    from systemd.daemon import notify as _sd_notify
except ImportError:
    _sd_notify = None  # type: ignore[assignment]

_sd_notify_autoscaler = _sd_notify


def _send_watchdog() -> None:
    """Send WATCHDOG=1 to systemd if available (issue #707).

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
