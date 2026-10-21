"""Host-local disk-full cordon executor (RD-OPS-017 / issue #2030)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from disk_full_cordon import (
    CordonAction,
    CordonActionKind,
    DiskFullCordonState,
    RunnerSnapshot,
    plan_cordon_tick,
)
from host_volume import probe_host_volume
from runner_disk_guard_policy import RunnerDiskGuardPolicy, load_runner_disk_guard_policy

log = logging.getLogger("dashboard.disk_full_cordon_runtime")

_STATE_PATH = Path(
    os.environ.get(
        "DISK_FULL_CORDON_STATE_PATH",
        "/var/lib/runner-dashboard/disk-full-cordon-state.json",
    )
)
_MAINTENANCE_PATH = Path(os.environ.get("RUNNER_MAINTENANCE_FLAG", "/var/lib/runner-scheduler/maintenance.json"))
_CLEANUP_BIN = Path(os.environ.get("RUNNER_CLEANUP_BIN", "/usr/local/bin/runner-cleanup"))


def load_cordon_state(path: Path | None = None) -> DiskFullCordonState:
    state_path = path or _STATE_PATH
    try:
        raw = state_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return DiskFullCordonState()
    if not isinstance(payload, dict):
        return DiskFullCordonState()
    phase = payload.get("phase", "normal")
    if phase not in ("normal", "tripping", "cordoned"):
        phase = "normal"
    tripped_at = payload.get("tripped_at")
    return DiskFullCordonState(
        phase=phase,
        tripped_at=float(tripped_at) if tripped_at is not None else None,
        cleanup_done=bool(payload.get("cleanup_done", False)),
    )


def save_cordon_state(state: DiskFullCordonState, path: Path | None = None) -> None:
    state_path = path or _STATE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": state.phase,
        "tripped_at": state.tripped_at,
        "cleanup_done": state.cleanup_done,
    }
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(state_path)


def cordon_status_dict(state: DiskFullCordonState | None = None) -> dict[str, Any]:
    """Dashboard-safe cordon status for fleet metrics."""
    current = state if state is not None else load_cordon_state()
    return {
        "active": current.phase != "normal",
        "phase": current.phase,
        "scheduling_inhibited": current.phase != "normal",
        "cleanup_done": current.cleanup_done,
        "tripped_at": current.tripped_at,
    }


def _set_maintenance(active: bool) -> None:
    if active:
        _MAINTENANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MAINTENANCE_PATH.write_text('{"reason":"disk-full-cordon"}\n', encoding="utf-8")
    else:
        _MAINTENANCE_PATH.unlink(missing_ok=True)


def _run_cleanup(dry_run: bool) -> None:
    if dry_run:
        log.info("disk-full cordon: dry-run skip runner-cleanup")
        return
    if not _CLEANUP_BIN.is_file():
        log.warning("disk-full cordon: cleanup binary missing at %s", _CLEANUP_BIN)
        return
    try:
        subprocess.run(
            [str(_CLEANUP_BIN), "--disk-guard"],
            check=False,
            timeout=1800,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("disk-full cordon: cleanup failed: %s", exc)


def apply_cordon_actions(
    actions: tuple[CordonAction, ...],
    *,
    dry_run: bool,
    stop_unit: Callable[[str, str], None],
) -> None:
    """Apply planned cordon actions (injectable for tests and autoscaler)."""
    for action in actions:
        if action.kind == CordonActionKind.SET_MAINTENANCE_ON:
            if not dry_run:
                _set_maintenance(True)
        elif action.kind == CordonActionKind.SET_MAINTENANCE_OFF:
            if not dry_run:
                _set_maintenance(False)
        elif action.kind == CordonActionKind.RUN_DISK_CLEANUP:
            _run_cleanup(dry_run)
        elif action.kind == CordonActionKind.STOP_RUNNER and action.unit:
            reason = "disk-full cordon (forced)" if action.force else "disk-full cordon drain"
            if dry_run:
                log.info("disk-full cordon: dry-run stop %s (%s)", action.unit, reason)
            else:
                stop_unit(action.unit, reason)


def build_runner_snapshots(
    units: list[str],
    unit_is_active: Callable[[str], bool],
    runner_is_busy: Callable[[str], bool],
) -> list[RunnerSnapshot]:
    return [RunnerSnapshot(unit=unit, active=unit_is_active(unit), busy=runner_is_busy(unit)) for unit in units]


def process_disk_full_cordon_tick(
    units: list[str],
    *,
    unit_is_active: Callable[[str], bool],
    runner_is_busy: Callable[[str], bool],
    stop_unit: Callable[[str, str], None],
    dry_run: bool,
    now: float,
    policy: RunnerDiskGuardPolicy | None = None,
    state_path: Path | None = None,
) -> bool:
    """Run one cordon tick. Returns True when normal autoscaler scaling should be skipped."""
    active_policy = policy or load_runner_disk_guard_policy()
    if not active_policy.enabled:
        return False

    host = probe_host_volume()
    free_gb = float(host.get("free_gb", 0.0))
    used_percent = float(host.get("percent", 0.0))

    state = load_cordon_state(state_path)
    runners = build_runner_snapshots(units, unit_is_active, runner_is_busy)
    result = plan_cordon_tick(
        state,
        free_gb=free_gb,
        used_percent=used_percent,
        policy=active_policy,
        runners=runners,
        now=now,
    )
    apply_cordon_actions(result.actions, dry_run=dry_run, stop_unit=stop_unit)
    save_cordon_state(result.state, state_path)
    if result.cordon_active:
        log.info(
            "disk-full cordon phase=%s free=%.1fGB used=%.1f%% actions=%d",
            result.state.phase,
            free_gb,
            used_percent,
            len(result.actions),
        )
    return result.cordon_active


def export_state_for_tests() -> dict[str, Any]:
    """Test helper: serialize current persisted state."""
    return asdict(load_cordon_state())
