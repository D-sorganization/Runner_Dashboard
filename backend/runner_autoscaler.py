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
import platform
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import TextIO

import yaml

try:
    import psutil
except ImportError:  # psutil is optional at import time; raise on use
    psutil = None  # type: ignore[assignment]

try:
    from dashboard_config.timeouts import HttpTimeout, ResourceThreshold
except ImportError:  # When deployed standalone the package may not be on path.
    HttpTimeout = None  # type: ignore[assignment,misc]
    ResourceThreshold = None  # type: ignore[assignment,misc]

log = logging.getLogger("runner-autoscaler")
logging.basicConfig(
    level=os.environ.get("AUTOSCALER_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
_lock_fd: TextIO | None = None


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

# Issue #651: defense-in-depth busy signal. The Runner.Worker child of MainPID
# is the primary signal, but there is a 1-2s race window between job pickup
# (Listener has accepted a job, written `_work/_temp/_runner_file_commands/`)
# and Worker fork. During that window we'd otherwise misread the unit as idle
# and kill it, leaving residue that breaks the next allocation. The lockfile
# written by `deploy/runner-hooks/job-started.sh` closes one half of the
# window; the listener-log quietude check below closes the other half.
RUNNER_BUSY_LOCK_DIR = Path(os.environ.get("RUNNER_BUSY_LOCK_DIR", "/var/run/runner-busy"))
RUNNER_BUSY_LOCK_MAX_AGE_SECONDS = _env_int(
    "RUNNER_BUSY_LOCK_MAX_AGE_SECONDS", 24 * 60 * 60
)  # 24h — stale lockfiles (Worker killed mid-job, never wrote completion hook)
# are GC'd by deploy/runner-cleanup.sh; the autoscaler treats them as "stale,
# not busy" to avoid permanently locking out a runner.

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


_SYSTEMCTL_TIMEOUT_S = HttpTimeout.SYSTEMCTL_S if HttpTimeout else 5


def _unit_is_active(unit: str) -> bool:
    r = subprocess.run(
        ["systemctl", "is-active", "--quiet", unit],
        check=False,
        timeout=_SYSTEMCTL_TIMEOUT_S,
    )
    return r.returncode == 0


def _unit_state(unit: str) -> tuple[str, str]:
    """Return (ActiveState, SubState) for *unit* from systemctl, or ('', '')."""
    r = subprocess.run(
        ["systemctl", "show", unit, "--property=ActiveState,SubState"],
        capture_output=True,
        text=True,
        timeout=_SYSTEMCTL_TIMEOUT_S,
        check=False,
    )
    active_state = ""
    sub_state = ""
    for line in (r.stdout or "").splitlines():
        if line.startswith("ActiveState="):
            active_state = line.split("=", 1)[1].strip()
        elif line.startswith("SubState="):
            sub_state = line.split("=", 1)[1].strip()
    return active_state, sub_state


def _runner_name_for_unit(unit: str) -> str:
    """Extract the runner name from a unit (the last dotted segment before .service).

    Example: ``actions.runner.D-sorganization.d-sorg-local-ControlTower-3.service``
    → ``d-sorg-local-ControlTower-3``.
    """
    # The unit prefix is ``actions.runner.<org>.``. Whatever follows up to the
    # ``.service`` suffix is the runner name. We use rpartition so a future
    # rename that adds extra dots to the org segment doesn't break us.
    if not unit.endswith(".service"):
        return unit
    stem = unit[: -len(".service")]
    return stem.rpartition(".")[2] or stem


def _runner_busy_via_lockfile(unit: str) -> bool:
    """Return True if a fresh job-pickup lockfile exists for *unit*.

    Issue #651 defense-in-depth signal. The Runner.Worker child of MainPID is
    the primary busy signal but races with job pickup; see
    ``deploy/runner-hooks/job-started.sh`` for the contract. A lockfile older
    than ``RUNNER_BUSY_LOCK_MAX_AGE_SECONDS`` is treated as stale (Worker died
    mid-job without firing the completion hook) and the runner is NOT
    considered busy on its account — the cleanup pass will GC it.
    """
    runner_name = _runner_name_for_unit(unit)
    lock = RUNNER_BUSY_LOCK_DIR / f"{runner_name}.lock"
    try:
        stat = lock.stat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.debug("lockfile stat failed unit=%s path=%s err=%s", unit, lock, exc)
        return False
    age = time.time() - stat.st_mtime
    if age > RUNNER_BUSY_LOCK_MAX_AGE_SECONDS:
        log.info(
            "stale lockfile (age=%.0fs > max=%ds) — not treating as busy unit=%s",
            age,
            RUNNER_BUSY_LOCK_MAX_AGE_SECONDS,
            unit,
        )
        return False
    return True


def _runner_is_busy(unit: str) -> bool:
    """Best-effort: does the runner have an active job?

    The GitHub Actions runner creates a ``Runner.Worker`` child process while
    executing a job.  We layer three detection strategies; ANY positive signal
    counts as busy:

    1. **Lockfile path** (issue #651) — the runner's JOB_STARTED hook writes
       ``/var/run/runner-busy/<runner-name>.lock``. This catches the brief
       window where the Worker exists but psutil hasn't yet seen it as a
       child of MainPID, and it persists across transient process-tree
       inspection failures. Stale lockfiles (>24h) are ignored.

    2. **MainPID path** — ask systemd for the unit's MainPID, then walk the
       process tree looking for a ``Runner.Worker`` child.  This is the most
       direct signal once the Worker has forked.

    3. **ActiveState/SubState fallback** — when MainPID is 0 (transient
       restart, brief crash, listener mid-reconfig), the main-PID path gives
       a false negative.  Instead we query ActiveState and SubState: if the
       unit is ``active/running`` we conservatively return *True* so the
       autoscaler never kills a runner that may be mid-job.  Err on the side
       of safety.

    If all strategies are inconclusive return *False* only when there is clear
    evidence the unit is inactive (e.g., ActiveState=inactive).
    """
    # ── Strategy 0: lockfile (defense-in-depth for the pickup race) ─────────
    if _runner_busy_via_lockfile(unit):
        return True

    # ── Strategy 1: MainPID-based child scan ─────────────────────────────────
    r = subprocess.run(
        ["systemctl", "show", unit, "--property=MainPID", "--value"],
        capture_output=True,
        text=True,
        timeout=_SYSTEMCTL_TIMEOUT_S,
        check=False,
    )
    pid_str = (r.stdout or "").strip()

    main_pid: int | None = None
    try:
        candidate = int(pid_str)
        if candidate > 0:
            main_pid = candidate
    except (ValueError, TypeError):
        pass

    if main_pid is not None and psutil is not None:
        try:
            proc = psutil.Process(main_pid)
            for child in proc.children(recursive=True):
                try:
                    if "Runner.Worker" in child.name() or "Runner.Worker" in " ".join(child.cmdline()):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        # MainPID was valid but no Runner.Worker child found — not busy.
        return False

    # ── Strategy 2: ActiveState/SubState fallback (MainPID == 0) ─────────────
    # MainPID=0 is a transient state: the listener is restarting, or systemd
    # hasn't yet registered the PID.  Never assume "not busy" in this window.
    active_state, sub_state = _unit_state(unit)
    log.debug(
        "_runner_is_busy fallback unit=%s MainPID=%s ActiveState=%s SubState=%s",
        unit,
        pid_str or "?",
        active_state,
        sub_state,
    )
    if active_state == "active" and sub_state == "running":
        # Unit is alive; we cannot confirm idleness without a valid PID, so
        # treat as busy to avoid interrupting a potential in-flight job.
        return True
    if active_state in ("inactive", "failed", "deactivating"):
        return False
    # Unknown / activating / reloading — be conservative.
    return True


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
        load1 = os.getloadavg()[0]  # type: ignore[attr-defined]
    except OSError:
        load1 = 0.0
    cores = psutil.cpu_count(logical=True) or 1
    disk_path = RUNNER_BASE_DIR if os.path.exists(RUNNER_BASE_DIR) else "/"
    usage = shutil.disk_usage(disk_path)
    disk_percent = usage.used / usage.total * 100
    disk_free_gb = usage.free / (1024**3)
    return cpu, mem, load1 / cores, disk_percent, disk_free_gb


_lock_fd = None


def main() -> None:
    if psutil is None:
        log.error("psutil not installed; cannot run autoscaler")
        raise SystemExit(2)

    try:
        import fcntl
        import sys

        global _lock_fd
        # Walk a small candidate list; the first writable path wins. The old
        # code hard-coded /var/run with a directory-existence check, which is
        # wrong when the service runs as a non-root user (the typical
        # deployment): /run exists but isn't writable for the runner user, so
        # the open() failed with PermissionError → caught as OSError → service
        # exit(75) every poll → systemd crash-loop. See d-sorg-local-ControlTower
        # post-2026-05-18 deploy for an instance.
        lock_path = ""
        last_err: OSError | None = None
        for candidate in (
            os.environ.get("AUTOSCALER_LOCK_PATH"),
            "/var/run/runner-autoscaler.lock",
            f"/run/user/{os.getuid()}/runner-autoscaler.lock",
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
            leased = _leased_runners()
            idle_active = [u for u in active if u not in busy and not any(r in u for r in leased)]

            if leased:
                log.info("Detected %d active leases: %s", len(leased), ", ".join(sorted(leased)))

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
