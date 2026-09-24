"""Staff Hub scheduling API (epic #1192, issue #1196).

Routes (all under ``/api/staff``):
  GET /holds       The holds list (locked policies the scheduler refuses to run against).
  PUT /holds       Replace the holds list.
  GET /schedule    Per role: next fire, window, blocking hold, budget, last fired.

Auth mirrors ``routers/staff.py``: reads ``require_fleet_peer``, mutations
``require_orchestrator_peer``. ``start_scheduler()`` is the server-startup
hook, gated by ``STAFF_SCHEDULER_ENABLED`` (default ``1``).
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, Field
from staff.holds import MAX_TEXT
from staff.scheduler import get_scheduler

log = logging.getLogger("dashboard.staff.schedule")
router = APIRouter(prefix="/api/staff", tags=["staff"])

SCHEDULER_ENABLED_ENV = "STAFF_SCHEDULER_ENABLED"
MAX_HOLDS = 200


class HoldBody(BaseModel):
    """One hold as sent by the operator console. DbC: the JSON shape of ``staff.holds.Hold``."""

    id: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    set_on: str = Field(default="", max_length=40)
    lifted_when: str = Field(default="", max_length=500)
    applies_to: list[str] = Field(default_factory=lambda: ["*"], max_length=50)
    active: bool = True


class HoldsBody(BaseModel):
    holds: list[HoldBody] = Field(max_length=MAX_HOLDS)


def scheduler_enabled() -> bool:
    return os.environ.get(SCHEDULER_ENABLED_ENV, "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
        "",
    )


def start_scheduler() -> None:
    """Reconcile orphaned staff runs and start the background ticker once. Called by ``server.py`` on startup."""
    try:
        from staff.reconcile import reconcile_orphaned_runs
        from staff.runner import get_runner

        reconcile_orphaned_runs(get_runner())
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to reconcile orphaned staff runs on startup: %s", exc)

    if not scheduler_enabled():
        log.info("staff scheduler disabled by %s", SCHEDULER_ENABLED_ENV)
        return
    get_scheduler().start()


@router.get("/holds")
async def get_holds(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    holds = get_scheduler().holds
    return {"holds": [h.to_dict() for h in holds.load()], "path": str(holds.path)}


@router.put("/holds")
async def put_holds(body: HoldsBody, caller: Principal = Depends(require_scope("staff.holds.write"))) -> dict[str, Any]:
    """Replace the holds list. Postcondition: the file on disk equals the response."""
    holds = get_scheduler().holds
    try:
        saved = holds.replace([h.model_dump() for h in body.holds])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    caller_str = format_caller(caller)
    log.info("staff: holds replaced by %s (%d holds)", caller_str, len(saved))
    return {"holds": [h.to_dict() for h in saved], "path": str(holds.path)}


@router.get("/schedule")
async def get_schedule(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    sched = get_scheduler()
    now = datetime.now(UTC)
    return {
        "machine": sched.runner.machine,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "enabled": scheduler_enabled(),
        "running": sched.running,
        "tick_seconds": sched.tick_seconds,
        "roles": sched.status(now),
    }
