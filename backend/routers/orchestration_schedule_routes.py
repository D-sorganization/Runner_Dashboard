# ruff: noqa: B008
"""Fleet runner schedule and capacity routes extracted from routers/orchestration.py."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_scope

if TYPE_CHECKING:
    from collections.abc import Callable

router = APIRouter(tags=["orchestration"])

_get_runner_capacity_snapshot: Callable | None = None
_validate_runner_schedule: Callable | None = None
_write_runner_schedule_config: Callable | None = None
_runner_scheduler_apply_command: Callable | None = None

_runner_scheduler_bin: str = ""
_runner_schedule_config: Path | None = None
_runner_scheduler_state: Path | None = None
_runner_base_dir: Path | None = None


def set_dependencies(
    get_runner_capacity_snapshot: Callable,
    validate_runner_schedule: Callable,
    write_runner_schedule_config: Callable,
    runner_scheduler_apply_command: Callable,
    runner_scheduler_bin: str,
    runner_schedule_config: Path,
    runner_scheduler_state: Path,
    runner_base_dir: Path,
) -> None:
    """Wire server.py helpers into this route module."""
    global _get_runner_capacity_snapshot, _validate_runner_schedule  # noqa: PLW0603
    global _write_runner_schedule_config, _runner_scheduler_apply_command  # noqa: PLW0603
    global _runner_scheduler_bin, _runner_schedule_config  # noqa: PLW0603
    global _runner_scheduler_state, _runner_base_dir  # noqa: PLW0603
    _get_runner_capacity_snapshot = get_runner_capacity_snapshot
    _validate_runner_schedule = validate_runner_schedule
    _write_runner_schedule_config = write_runner_schedule_config
    _runner_scheduler_apply_command = runner_scheduler_apply_command
    _runner_scheduler_bin = runner_scheduler_bin
    _runner_schedule_config = runner_schedule_config
    _runner_scheduler_state = runner_scheduler_state
    _runner_base_dir = runner_base_dir


@router.get("/api/fleet/schedule")
async def get_runner_schedule() -> dict:
    """Return this machine's local runner capacity schedule and live state."""
    return await asyncio.to_thread(_get_runner_capacity_snapshot)  # type: ignore[arg-type]


@router.get("/api/fleet/capacity")
async def get_fleet_capacity() -> dict:
    """Compatibility endpoint for dashboard capacity summaries."""
    return await asyncio.to_thread(_get_runner_capacity_snapshot)  # type: ignore[arg-type]


@router.post("/api/fleet/schedule")
async def update_runner_schedule(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict:
    """Update this machine's local runner capacity schedule."""
    from security import safe_subprocess_env  # noqa: PLC0415

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="schedule payload must be an object")
    try:
        config = _validate_runner_schedule(body.get("schedule", body))  # type: ignore[misc]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _write_runner_schedule_config(config)  # type: ignore[misc]
    apply_now = bool(body.get("apply", False))
    apply_result: dict[str, object] | None = None
    if apply_now and Path(_runner_scheduler_bin).exists():
        env = safe_subprocess_env()
        env["RUNNER_ROOT"] = str(_runner_base_dir)
        env["RUNNER_SCHEDULE_CONFIG"] = str(_runner_schedule_config)
        env["RUNNER_SCHEDULER_STATE"] = str(_runner_scheduler_state)
        apply_cmd = _runner_scheduler_apply_command()  # type: ignore[misc]
        result = await asyncio.to_thread(
            subprocess.run,
            apply_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
        apply_result = {
            "returncode": result.returncode,
            "stdout": result.stdout.strip()[:1000],
            "stderr": result.stderr.strip()[:1000],
        }
        if result.returncode != 0:
            error = apply_result["stderr"] or apply_result["stdout"]
            raise HTTPException(
                status_code=500,
                detail=f"Schedule saved, but apply failed: {error}",
            )
    return {
        "saved": True,
        "applied": apply_now,
        "apply_result": apply_result,
        **await asyncio.to_thread(_get_runner_capacity_snapshot),  # type: ignore[arg-type]
    }


_MAINTENANCE_UNIT = "runner-maintenance-drain"


def _maintenance_command(action: str, *, reboot: bool) -> list[str]:
    """Build the privileged scheduler invocation for a maintenance action (#1929).

    ``drain`` can run for up to an hour (it waits for in-flight jobs), so it is
    launched as a transient systemd unit and returns immediately; progress is
    read back through ``GET /api/fleet/schedule`` (``state.maintenance``).
    """
    assert action in {"drain", "resume"}, f"unknown maintenance action: {action}"  # noqa: S101
    scheduler_env = [
        f"--setenv=RUNNER_ROOT={_runner_base_dir}",
        f"--setenv=RUNNER_SCHEDULE_CONFIG={_runner_schedule_config}",
        f"--setenv=RUNNER_SCHEDULER_STATE={_runner_scheduler_state}",
    ]
    if action == "resume":
        return [
            "sudo",
            "-n",
            "systemd-run",
            "--wait",
            "--collect",
            *scheduler_env,
            _runner_scheduler_bin,
            "--apply",
            "--resume",
        ]
    flags = ["--apply", "--drain", *(["--reboot"] if reboot else [])]
    return [
        "sudo",
        "-n",
        "systemd-run",
        f"--unit={_MAINTENANCE_UNIT}",
        "--collect",
        *scheduler_env,
        _runner_scheduler_bin,
        *flags,
    ]


@router.post("/api/fleet/maintenance", include_in_schema=False)
async def fleet_maintenance(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict:
    """Drain this host's runners for maintenance, or lift the hold (#1929).

    ``{"action": "drain", "reboot": false}`` holds the target at 0, stops each
    runner the moment it gaps between jobs (never mid-job), and optionally
    reboots once fully drained. ``{"action": "resume"}`` clears the hold. Kept
    out of the OpenAPI schema so the vendored snapshot is unaffected.
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="maintenance payload must be an object")
    action = body.get("action")
    reboot = body.get("reboot", False)
    if action not in ("drain", "resume"):
        raise HTTPException(status_code=400, detail="action must be 'drain' or 'resume'")
    if not isinstance(reboot, bool) or (reboot and action != "drain"):
        raise HTTPException(status_code=400, detail="reboot must be a boolean and is only valid with drain")
    if not Path(_runner_scheduler_bin).exists():
        raise HTTPException(status_code=503, detail=f"{_runner_scheduler_bin} is not installed")
    from security import safe_subprocess_env  # noqa: PLC0415

    result = await asyncio.to_thread(
        subprocess.run,
        _maintenance_command(action, reboot=reboot),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=safe_subprocess_env(),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise HTTPException(status_code=500, detail=f"maintenance {action} failed: {detail}")
    return {
        "action": action,
        "reboot": reboot,
        "requested_by": principal.id,
        **await asyncio.to_thread(_get_runner_capacity_snapshot),  # type: ignore[arg-type]
    }
