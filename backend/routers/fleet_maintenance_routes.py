# ruff: noqa: B008
"""Fleet maintenance (drain / resume) route (#1936).

PRIVILEGED: ``drain`` with ``reboot`` reboots this host. Safety properties:

* The command is a list handed to ``subprocess.run`` (never a shell) and is built
  ONLY from the closed ``MaintenanceAction`` enum and a validated ``bool``.
  No request string ever reaches argv.
* Every path (scheduler binary, config, state, runner root) comes from server
  config via ``orchestration_schedule_routes.scheduler_paths()``, never from the request.
* Scope ``fleet.control`` is required (same dependency as the other fleet.control routes).
* An audit entry naming the principal is written BEFORE execution and again with the outcome.
* Sudoers scope: see ``docs/runbooks/fleet-maintenance.md``.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import enum
import logging
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import orchestration_audit as _audit
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_scope
from routers import orchestration_schedule_routes as _schedule
from security import safe_subprocess_env, sanitize_log_value

log = logging.getLogger("dashboard.fleet_maintenance")
router = APIRouter(tags=["orchestration"])

DRAIN_UNIT = "runner-maintenance-drain"
_ALLOWED_KEYS = frozenset({"action", "reboot"})
_LAUNCH_TIMEOUT_S = 30  # drain returns at unit start; resume waits for a fast command
_DETAIL_LIMIT = 500


class MaintenanceAction(enum.Enum):
    DRAIN = "drain"
    RESUME = "resume"


def parse_maintenance_request(body: object) -> tuple[MaintenanceAction, bool]:
    """Validate a request body. Raises ``ValueError`` (mapped to 400) for anything not allowed.

    Pre: none.  Post: returns an enum member and a real ``bool``; nothing else survives.
    """
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    unknown = set(body) - _ALLOWED_KEYS
    if unknown:
        raise ValueError("unknown field(s) in request")
    raw_action = body.get("action")
    if not isinstance(raw_action, str):
        raise ValueError("action is required: 'drain' or 'resume'")
    try:
        action = MaintenanceAction(raw_action)
    except ValueError:
        raise ValueError("action must be 'drain' or 'resume'") from None
    reboot = body.get("reboot", False)
    if not isinstance(reboot, bool):
        raise ValueError("reboot must be a boolean")
    if reboot and action is not MaintenanceAction.DRAIN:
        raise ValueError("reboot is only valid with action 'drain'")
    return action, reboot


def build_maintenance_command(
    action: MaintenanceAction,
    reboot: bool,
    *,
    scheduler_bin: str,
    config: Path,
    state: Path,
    runner_root: Path,
) -> list[str]:
    """Build the exact argv. Inputs are an enum, a bool and server-configured paths only."""
    if action is MaintenanceAction.DRAIN:
        # Drain can take up to an hour: transient unit, no --wait.
        head = ["sudo", "-n", "systemd-run", f"--unit={DRAIN_UNIT}", "--collect"]
        tail = ["--apply", "--drain"] + (["--reboot"] if reboot else [])
    else:
        head = ["sudo", "-n", "systemd-run", "--wait", "--collect"]
        tail = ["--apply", "--resume"]
    env = [
        f"--setenv=RUNNER_ROOT={runner_root}",
        f"--setenv=RUNNER_SCHEDULE_CONFIG={config}",
        f"--setenv=RUNNER_SCHEDULER_STATE={state}",
    ]
    return [*head, *env, scheduler_bin, *tail]


def _audit_entry(
    phase: str, principal: Principal, action: MaintenanceAction, reboot: bool, audit_id: str, **extra: Any
) -> dict[str, Any]:
    return {
        "audit_id": audit_id,
        "orchestration_type": "fleet_maintenance",
        "action": f"fleet.maintenance.{action.value}",
        "phase": phase,
        "reboot": reboot,
        "requested_by": principal.id,
        "recorded_at": _dt.datetime.now(_dt.UTC).isoformat().replace("+00:00", "Z"),
        **extra,
    }


@router.post("/api/fleet/maintenance")
async def fleet_maintenance(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),
) -> dict[str, Any]:
    """Drain (optionally reboot) or resume this machine's runners. Progress: GET /api/fleet/schedule."""
    try:
        action, reboot = parse_maintenance_request(await request.json())
    except ValueError as exc:  # includes malformed JSON (JSONDecodeError)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    scheduler_bin, config, state, runner_root = _schedule.scheduler_paths()
    if not Path(scheduler_bin).exists():
        raise HTTPException(status_code=503, detail="runner-scheduler is not installed on this machine")
    argv = build_maintenance_command(
        action, reboot, scheduler_bin=scheduler_bin, config=config, state=state, runner_root=runner_root
    )

    audit_id = uuid4().hex
    log.info(
        "audit: fleet_maintenance action=%s reboot=%s by=%s",
        action.value,
        reboot,
        sanitize_log_value(principal.id),
    )
    await _audit.append_orchestration_audit(_audit_entry("requested", principal, action, reboot, audit_id))
    failure = await _run(argv)
    outcome = "failed" if failure else "started"
    await _audit.append_orchestration_audit(
        _audit_entry("completed", principal, action, reboot, audit_id, outcome=outcome)
    )
    if failure:
        raise HTTPException(status_code=500, detail=f"maintenance {action.value} failed: {failure}")
    return {"action": action.value, "reboot": reboot, "started": True, "audit_id": audit_id}


async def _run(argv: list[str]) -> str:
    """Run ``argv`` (no shell). Returns "" on success, else a bounded failure description."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            argv,
            capture_output=True,
            text=True,
            timeout=_LAUNCH_TIMEOUT_S,
            check=False,
            shell=False,
            env=safe_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)[:_DETAIL_LIMIT] or type(exc).__name__
    if result.returncode == 0:
        return ""
    detail = (result.stderr or result.stdout or "").strip()[:_DETAIL_LIMIT]
    return detail or f"exit code {result.returncode}"
