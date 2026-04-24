#!/usr/bin/env python3
"""Performance-aware runner auto-scaler.

Monitors the local machine's CPU, memory, and load, and takes idle runners
offline (by stopping their systemd unit) when thresholds are exceeded, and
brings them back online when load drops. The goal is to avoid making the
host unusable when the fleet is saturated.

Only *idle* runners are stopped; a busy runner is never interrupted
mid-job. A minimum number of runners (``MIN_ONLINE_RUNNERS``) is always
kept running so at least one lane stays available for small jobs.

Runs as a separate systemd unit (``runner-autoscaler.service``) every
``POLL_INTERVAL`` seconds. See ``deploy/runner-autoscaler.service`` for
the unit file.

Environment variables:
    AUTOSCALER_CPU_HIGH        default 85  — scale down above this % sustained
    AUTOSCALER_CPU_LOW         default 40  — scale back up below this %
    AUTOSCALER_MEM_HIGH        default 85  — memory headroom threshold
    AUTOSCALER_DISK_HIGH       default 92  — disk usage threshold
    AUTOSCALER_DISK_MIN_FREE_GB default 25 — minimum free disk headroom
    AUTOSCALER_LOAD_PER_CORE   default 1.5 — sustained load1 / cpu_count
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
import platform
import shutil
import subprocess
import time
from collections import deque

try:
    import psutil
except ImportError:  # psutil is optional at import time; raise on use
    psutil = None

log = logging.getLogger("runner-autoscaler")
logging.basicConfig(
    level=os.environ.get("AUTOSCALER_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


CPU_HIGH = _env_float("AUTOSCALER_CPU_HIGH", 85.0)
CPU_LOW = _env_float("AUTOSCALER_CPU_LOW", 40.0)
MEM_HIGH = _env_float("AUTOSCALER_MEM_HIGH", 85.0)
DISK_HIGH = _env_float("AUTOSCALER_DISK_HIGH", 92.0)
DISK_MIN_FREE_GB = _env_float("AUTOSCALER_DISK_MIN_FREE_GB", 25.0)
LOAD_PER_CORE = _env_float("AUTOSCALER_LOAD_PER_CORE", 1.5)
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

HOSTNAME = platform.node()


def _list_runner_units() -> list[str]:
    """Enumerate this machine's GitHub Actions runner systemd units."""
    try:
        r = subprocess.run(
            ["systemctl", "list-unit-files", "--type=service", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("systemctl list failed: %s", exc)
        return []
    units = []
    for line in r.stdout.splitlines():
        name = line.split()[0] if line else ""
        if name.startswith("actions.runner.") and name.endswith(".service"):
            units.append(name)
    return sorted(units)


def _unit_is_active(unit: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False, timeout=5)
    return r.returncode == 0


def _runner_is_busy(unit: str) -> bool:
    """Best-effort: does the runner have an active job?

    The GitHub Actions runner creates a `.runner_worker` lock file inside its
    install directory while executing a job. We look for a child Runner.Worker
    process under the unit's cgroup as the authoritative signal.
    """
    # Find the main PID of the unit
    r = subprocess.run(
        ["systemctl", "show", unit, "--property=MainPID", "--value"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    pid_str = (r.stdout or "").strip()
    if not pid_str or pid_str == "0":
        return False
    try:
        main_pid = int(pid_str)
    except ValueError:
        return False
    if psutil is None:
        return False
    try:
        proc = psutil.Process(main_pid)
        for child in proc.children(recursive=True):
            try:
                if "Runner.Worker" in child.name() or "Runner.Worker" in " ".join(child.cmdline()):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return False


def _stop_unit(unit: str) -> bool:
    if DRY_RUN:
        log.info("[dry-run] would stop %s", unit)
        return True
    r = subprocess.run(
        ["sudo", "-n", "systemctl", "stop", unit],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        log.warning("Failed to stop %s: %s", unit, r.stderr.strip()[:200])
        return False
    log.warning("Autoscaler STOPPED %s (host overloaded)", unit)
    return True


def _start_unit(unit: str) -> bool:
    if DRY_RUN:
        log.info("[dry-run] would start %s", unit)
        return True
    r = subprocess.run(
        ["sudo", "-n", "systemctl", "start", unit],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        log.warning("Failed to start %s: %s", unit, r.stderr.strip()[:200])
        return False
    log.info("Autoscaler STARTED %s (host recovered)", unit)
    return True


def _scheduled_desired_count(default: int) -> int:
    """Read the schedule service's current desired capacity when installed."""
    if not os.path.exists(RUNNER_SCHEDULER_BIN):
        return default
    try:
        env = os.environ.copy()
        env["RUNNER_SCHEDULE_CONFIG"] = RUNNER_SCHEDULE_CONFIG
        result = subprocess.run(
            [RUNNER_SCHEDULER_BIN, "--dry-run", "--json"],
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
    if psutil is None:
        raise RuntimeError("psutil is required for runner-autoscaler")
    cpu = psutil.cpu_percent(interval=1.0)
    mem = psutil.virtual_memory().percent
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = 0.0
    cores = psutil.cpu_count(logical=True) or 1
    disk_path = RUNNER_BASE_DIR if os.path.exists(RUNNER_BASE_DIR) else "/"
    usage = shutil.disk_usage(disk_path)
    disk_percent = usage.used / usage.total * 100
    disk_free_gb = usage.free / (1024**3)
    return cpu, mem, load1 / cores, disk_percent, disk_free_gb


def main() -> None:
    if psutil is None:
        log.error("psutil not installed; cannot run autoscaler")
        raise SystemExit(2)
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
    history_len = max(3, SUSTAIN_SECS // max(POLL_SECONDS, 1))
    samples: deque[tuple[float, float, float, float, float]] = deque(maxlen=history_len)

    while True:
        try:
            cpu, mem, load, disk, disk_free = _sample()
            samples.append((cpu, mem, load, disk, disk_free))
            if len(samples) < history_len:
                time.sleep(POLL_SECONDS)
                continue

            # Averages over the sustain window
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
            idle_active = [u for u in active if u not in busy]

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

            if overloaded and len(active) > MIN_ONLINE:
                # Scale down: stop up to MAX_STEP idle runners, never going below MIN_ONLINE
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
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
