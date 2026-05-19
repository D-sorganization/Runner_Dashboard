"""Systemd unit enumeration and lifecycle helpers for the runner auto-scaler.

Provides discovery, state inspection, and start/stop control of GitHub Actions
runner systemd units. All subprocess calls use structured subprocess.run (no
shell=True) and honour the configured timeout from autoscaler_config.
"""

from __future__ import annotations

import logging
import subprocess

from autoscaler_config import (
    _SYSTEMCTL_TIMEOUT_S,
    DRY_RUN,
)

log = logging.getLogger("runner-autoscaler")


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


def _stop_unit(unit: str) -> bool:
    """Stop *unit* via systemd, respecting DRY_RUN mode.

    Returns True on success (or in dry-run), False if systemctl reports failure.
    """
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
    """Start *unit* via systemd, respecting DRY_RUN mode.

    Returns True on success (or in dry-run), False if systemctl reports failure.
    """
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
