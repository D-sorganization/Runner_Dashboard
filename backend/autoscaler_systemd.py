"""Systemd unit enumeration and lifecycle helpers for the runner auto-scaler.

Provides discovery, state inspection, and start/stop control of GitHub Actions
runner systemd units. All subprocess calls use structured subprocess.run (no
shell=True) and honour the configured timeout from autoscaler_config.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys

from autoscaler_config import (
    _SYSTEMCTL_TIMEOUT_S,
    DRY_RUN,
)

log = logging.getLogger("runner-autoscaler")
_MIN_SAFE_STOP_TIMEOUT_SECONDS = 120


def _dry_run_enabled() -> bool:
    runner_autoscaler = sys.modules.get("runner_autoscaler")
    if runner_autoscaler is not None:
        return DRY_RUN or bool(getattr(runner_autoscaler, "DRY_RUN", DRY_RUN))
    return DRY_RUN


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
    """Return True when *unit* is currently active (running) according to systemd."""
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


def _runner_workdir_for_unit(unit: str) -> str:
    """Resolve the unit's WorkingDirectory from systemd.

    Returns empty string if the unit isn't known or doesn't have a working
    directory configured. Used by ``_runner_busy_via_pickup_dir`` to find
    the runner's `_work/_temp/_runner_file_commands/` location without
    assuming a directory-naming convention.
    """
    r = subprocess.run(
        ["systemctl", "show", unit, "--property=WorkingDirectory", "--value"],
        capture_output=True,
        text=True,
        timeout=_SYSTEMCTL_TIMEOUT_S,
        check=False,
    )
    return (r.stdout or "").strip()


def _systemd_timespan_seconds(value: str) -> float:
    """Parse common systemd time spans into seconds."""
    text = value.strip()
    if not text:
        return 0.0
    if text.isdigit():
        raw_value = float(text)
        return raw_value / 1_000_000 if raw_value > 10_000 else raw_value

    total = 0.0
    multipliers = {
        "usec": 0.000001,
        "us": 0.000001,
        "ms": 0.001,
        "s": 1.0,
        "min": 60.0,
        "h": 3600.0,
        "d": 86400.0,
    }
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(usec|us|ms|s|min|h|d)", text):
        total += float(amount) * multipliers[unit]
    return total


def _unit_has_safe_stop_contract(unit: str) -> bool:
    """Return True when stopping *unit* should not kill active jobs.

    The autoscaler may only stop runner units whose effective systemd
    configuration has the #640/#679 drain contract loaded. If the host was not
    redeployed and still has the unsafe default, refusing scale-down is safer
    than terminating a checkout or test step mid-job.
    """
    try:
        r = subprocess.run(
            ["systemctl", "show", unit, "--property=KillMode,TimeoutStopUSec"],
            capture_output=True,
            text=True,
            timeout=_SYSTEMCTL_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Could not inspect stop contract for %s: %s", unit, exc)
        return False

    props: dict[str, str] = {}
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value.strip()

    kill_mode = props.get("KillMode", "")
    timeout_seconds = _systemd_timespan_seconds(props.get("TimeoutStopUSec", ""))
    if kill_mode != "mixed" or timeout_seconds < _MIN_SAFE_STOP_TIMEOUT_SECONDS:
        log.error(
            "Refusing to stop %s: unsafe runner stop contract "
            "(KillMode=%s TimeoutStopUSec=%s, need KillMode=mixed and timeout>=%ss). "
            "Run deploy/install-runner-maintenance.sh or deploy/migrate-runner-units.sh.",
            unit,
            kill_mode or "?",
            props.get("TimeoutStopUSec", "?"),
            _MIN_SAFE_STOP_TIMEOUT_SECONDS,
        )
        return False
    return True


def _stop_unit(unit: str, *, reason: str = "host overloaded") -> bool:
    """Stop *unit* via systemd, respecting DRY_RUN mode.

    Returns True on success (or in dry-run), False if systemctl reports failure.

    ``reason`` is the operator-facing cause logged on a successful stop. It MUST
    reflect the branch that decided to stop: a scheduled-surplus trim and a
    genuine host-overload eviction are different events, and labelling every
    stop "host overloaded" (the old hardcoded string) made an idle-host
    schedule trim look like a CPU/memory emergency — the misleading half of the
    OGLaptop 2026-06-09 report.

    After every stop attempt (whether systemctl returns success or failure)
    invokes :func:`runner_state_cleanup.cleanup_runner_state` so that any
    ``$HOME/.gitconfig.lock`` orphaned by an abrupt SIGTERM does not poison
    the next job assigned to this host. See Runner_Dashboard#640.
    """
    if _dry_run_enabled():
        log.info("[dry-run] would stop %s", unit)
        return True
    if not _unit_has_safe_stop_contract(unit):
        return False
    # Issue #935: stop with ``--no-block``. The #640/#679 drain contract lets a
    # busy runner legally hold systemd's stop for up to TimeoutStopUSec (>=120s),
    # but the autoscaler's systemd watchdog only beats at the top/end of each
    # poll tick. A blocking ``systemctl stop`` that waits out a 120s drain
    # starves the watchdog, and systemd SIGABRTs the autoscaler mid-scale-down —
    # killing the service for correctly honouring the very stop contract it
    # requires. ``--no-block`` enqueues the stop job and returns immediately; the
    # drain proceeds in the background and the next poll tick re-reads unit state
    # (active → inactive) to confirm completion. The explicit timeout guards the
    # systemctl client call itself (job enqueue), never the drain.
    try:
        r = subprocess.run(
            ["sudo", "-n", "systemctl", "stop", "--no-block", unit],
            capture_output=True,
            text=True,
            timeout=_SYSTEMCTL_TIMEOUT_S,
            check=False,
        )
        success = r.returncode == 0
        if not success:
            log.warning("Failed to stop %s: %s", unit, r.stderr.strip()[:200])
        else:
            log.warning("Autoscaler STOPPED %s (%s)", unit, reason)
    except (OSError, subprocess.SubprocessError) as exc:
        # The enqueue itself failed (e.g. systemctl client timeout). The unit
        # may or may not have been signalled, so run cleanup on this path too —
        # the #640 ~/.gitconfig.lock contract requires cleanup on EVERY stop
        # attempt — and report failure.
        log.warning("Failed to enqueue stop for %s: %s", unit, exc)
        success = False

    # Recovery half of the stop contract — best-effort, never raises.
    try:
        from runner_state_cleanup import cleanup_runner_state  # noqa: PLC0415

        cleanup_runner_state(unit)
    except Exception as exc:  # noqa: BLE001 — must not break autoscaler loop
        log.warning("cleanup_runner_state failed for %s: %s", unit, exc)

    return success


def _start_unit(unit: str) -> bool:
    """Start *unit* via systemd, respecting DRY_RUN mode.

    Returns True on success (or in dry-run), False if systemctl reports failure.
    """
    if _dry_run_enabled():
        log.info("[dry-run] would start %s", unit)
        return True
    # Issue #935: ``--no-block`` so a slow unit start cannot starve the watchdog,
    # plus an explicit client-call timeout. Start is far quicker than a drained
    # stop, but the watchdog-starvation reasoning is identical, and the next tick
    # re-reads ActiveState to confirm the unit came up.
    try:
        r = subprocess.run(
            ["sudo", "-n", "systemctl", "start", "--no-block", unit],
            capture_output=True,
            text=True,
            timeout=_SYSTEMCTL_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Failed to enqueue start for %s: %s", unit, exc)
        return False
    if r.returncode != 0:
        log.warning("Failed to start %s: %s", unit, r.stderr.strip()[:200])
        return False
    log.info("Autoscaler STARTED %s (host recovered)", unit)
    return True
