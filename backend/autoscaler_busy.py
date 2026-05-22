"""Runner busy-detection logic for the runner auto-scaler.

Provides four layered strategies to determine whether a GitHub Actions runner
is actively processing a job before the autoscaler stops it.

Strategy ordering (any positive signal → busy):
  1. Pickup-directory mtime  — closes the pre-Worker race window (#651)
  2. Job-pickup lockfile      — defense-in-depth for transient psutil misses
  3. MainPID child scan       — direct psutil check for Runner.Worker process
  4. ActiveState/SubState     — conservative fallback when MainPID is 0
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from autoscaler_config import (
    _SYSTEMCTL_TIMEOUT_S,
    RUNNER_BUSY_LOCK_DIR,
    RUNNER_BUSY_LOCK_MAX_AGE_SECONDS,
    RUNNER_PICKUP_DIR_MAX_AGE_SECONDS,
    psutil,
)
from autoscaler_systemd import (
    _runner_name_for_unit,
    _runner_workdir_for_unit,
    _unit_state,
)

log = logging.getLogger("runner-autoscaler")
_DEFAULT_PSUTIL = psutil
_DEFAULT_RUNNER_WORKDIR_FOR_UNIT = _runner_workdir_for_unit
_DEFAULT_RUNNER_BUSY_LOCK_DIR = RUNNER_BUSY_LOCK_DIR
_DEFAULT_RUNNER_BUSY_LOCK_MAX_AGE_SECONDS = RUNNER_BUSY_LOCK_MAX_AGE_SECONDS
_DEFAULT_RUNNER_PICKUP_DIR_MAX_AGE_SECONDS = RUNNER_PICKUP_DIR_MAX_AGE_SECONDS


def _public_attr(name: str, current: Any, original: Any) -> Any:
    if current != original:
        return current
    runner_autoscaler = sys.modules.get("runner_autoscaler")
    if runner_autoscaler is None:
        return current
    return getattr(runner_autoscaler, name, current)


def _psutil_dep() -> Any:
    if psutil is not _DEFAULT_PSUTIL:
        return psutil
    runner_autoscaler = sys.modules.get("runner_autoscaler")
    if runner_autoscaler is not None:
        return getattr(runner_autoscaler, "psutil", psutil)
    return psutil


def _runner_workdir_for_unit_public(unit: str) -> str:
    if _runner_workdir_for_unit is not _DEFAULT_RUNNER_WORKDIR_FOR_UNIT:
        return str(_runner_workdir_for_unit(unit))
    runner_autoscaler = sys.modules.get("runner_autoscaler")
    if runner_autoscaler is not None:
        public_func = getattr(runner_autoscaler, "_runner_workdir_for_unit", None)
        if public_func is not None and public_func is not _DEFAULT_RUNNER_WORKDIR_FOR_UNIT:
            return str(public_func(unit))
    return _runner_workdir_for_unit(unit)


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
    lock_dir = Path(_public_attr("RUNNER_BUSY_LOCK_DIR", RUNNER_BUSY_LOCK_DIR, _DEFAULT_RUNNER_BUSY_LOCK_DIR))
    lock = lock_dir / f"{runner_name}.lock"
    try:
        stat = lock.stat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.debug("lockfile stat failed unit=%s path=%s err=%s", unit, lock, exc)
        return False
    age = time.time() - stat.st_mtime
    lock_max_age = int(
        _public_attr(
            "RUNNER_BUSY_LOCK_MAX_AGE_SECONDS",
            RUNNER_BUSY_LOCK_MAX_AGE_SECONDS,
            _DEFAULT_RUNNER_BUSY_LOCK_MAX_AGE_SECONDS,
        )
    )
    if age > lock_max_age:
        log.info(
            "stale lockfile (age=%.0fs > max=%ds) — not treating as busy unit=%s",
            age,
            lock_max_age,
            unit,
        )
        return False
    return True


def _runner_busy_via_pickup_dir(unit: str) -> bool:
    """Return True if the Listener is mid-pickup (issue #651 root race).

    The Listener writes files under ``_work/_temp/_runner_file_commands/``
    BEFORE forking the Worker. If the autoscaler kills in that 1-2s window:
    - MainPID has no Worker child (false negative on Strategy 3)
    - The job-pickup hook hasn't fired yet (false negative on Strategy 2)

    Checking the directory's mtime closes that window. A directory modified
    within ``RUNNER_PICKUP_DIR_MAX_AGE_SECONDS`` (default 30s) means the
    Listener is actively handing off — busy. Older = stale residue from a
    prior killed Worker, NOT busy (the cleanup pass GCs it).

    Why mtime rather than existence: a corrupted runner that we're trying
    to heal will have a stale `_runner_file_commands/` directory left over.
    Marking that as busy forever would prevent cleanup from ever touching
    the runner. mtime distinguishes active pickup (recent) from stale
    residue (old).
    """
    work_dir = _runner_workdir_for_unit_public(unit)
    if not work_dir:
        return False
    fc = Path(work_dir) / "_work" / "_temp" / "_runner_file_commands"
    try:
        stat = fc.stat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        log.debug("pickup-dir stat failed unit=%s path=%s err=%s", unit, fc, exc)
        return False
    age = time.time() - stat.st_mtime
    pickup_max_age = int(
        _public_attr(
            "RUNNER_PICKUP_DIR_MAX_AGE_SECONDS",
            RUNNER_PICKUP_DIR_MAX_AGE_SECONDS,
            _DEFAULT_RUNNER_PICKUP_DIR_MAX_AGE_SECONDS,
        )
    )
    if age <= pickup_max_age:
        log.info(
            "pickup-dir fresh (age=%.1fs <= %ds) — treating as busy unit=%s",
            age,
            pickup_max_age,
            unit,
        )
        return True
    return False


def _runner_busy_via_pickup_dir_public(unit: str) -> bool:
    runner_autoscaler = sys.modules.get("runner_autoscaler")
    if runner_autoscaler is not None:
        public_func = getattr(runner_autoscaler, "_runner_busy_via_pickup_dir", None)
        if public_func is not None and public_func is not _runner_busy_via_pickup_dir:
            return bool(public_func(unit))
    return _runner_busy_via_pickup_dir(unit)


def _runner_is_busy(unit: str) -> bool:
    """Best-effort: does the runner have an active job?

    The GitHub Actions runner creates a ``Runner.Worker`` child process while
    executing a job.  We layer FOUR detection strategies; ANY positive signal
    counts as busy:

    1. **Pickup-directory mtime** (issue #651 root race) — the Listener
       writes ``_work/_temp/_runner_file_commands/`` BEFORE forking the
       Worker. A recent mtime catches the pre-Worker race window that
       neither the lockfile (hook hasn't fired) nor the MainPID walk
       (no Worker child yet) can see.

    2. **Lockfile path** (issue #651 defense-in-depth) — the runner's
       JOB_STARTED hook writes ``/var/run/runner-busy/<runner-name>.lock``.
       This catches the inverse window where the Worker exists but psutil's
       child walk transiently misses it. Stale lockfiles (>24h) are ignored.

    3. **MainPID path** — ask systemd for the unit's MainPID, then walk the
       process tree looking for a ``Runner.Worker`` child.  This is the most
       direct signal once the Worker has forked and stabilized.

    4. **ActiveState/SubState fallback** — when MainPID is 0 (transient
       restart, brief crash, listener mid-reconfig), the main-PID path gives
       a false negative.  Instead we query ActiveState and SubState: if the
       unit is ``active/running`` we conservatively return *True* so the
       autoscaler never kills a runner that may be mid-job.

    If all strategies are inconclusive return *False* only when there is clear
    evidence the unit is inactive (e.g., ActiveState=inactive).
    """
    # ── Strategy 1: pickup-window directory (closes the pre-Worker race) ────
    if _runner_busy_via_pickup_dir_public(unit):
        return True

    # ── Strategy 2: lockfile (defense-in-depth for transient psutil misses) ─
    if _runner_busy_via_lockfile(unit):
        return True

    # ── Strategy 3: MainPID-based child scan ─────────────────────────────────
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

    psutil_mod = _psutil_dep()
    if main_pid is not None and psutil_mod is not None:
        try:
            proc = psutil_mod.Process(main_pid)
            for child in proc.children(recursive=True):
                try:
                    if "Runner.Worker" in child.name() or "Runner.Worker" in " ".join(child.cmdline()):
                        return True
                except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                    continue
        except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
            pass
        # MainPID was valid but no Runner.Worker child found — not busy.
        return False

    # ── Strategy 4: ActiveState/SubState fallback (MainPID == 0) ─────────────
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
