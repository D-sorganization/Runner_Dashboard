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
    return _get_runner_capacity_snapshot()  # type: ignore[misc]


@router.get("/api/fleet/capacity")
async def get_fleet_capacity() -> dict:
    """Compatibility endpoint for dashboard capacity summaries."""
    return _get_runner_capacity_snapshot()  # type: ignore[misc]


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
        **_get_runner_capacity_snapshot(),  # type: ignore[misc]
    }
