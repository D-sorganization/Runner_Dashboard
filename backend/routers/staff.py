"""Staff Hub API (epic #1192, issues #1194/#1195).

Routes (all under ``/api/staff``):
  GET  /roster                Roster: roles, provider availability, active counts.
  GET  /board                 Status monitor for this node: running/queued/recent, spend.
  GET  /runs                  Run history (filters: role, status, since, limit).
  GET  /runs/{id}             One run + its most recent events.
  GET  /runs/{id}/stream      Server-sent events: live output until the run ends.
  POST /{role}/run            Dispatch (or ``dry_run`` to preview the plan).
  POST /runs/{id}/cancel      Terminate a run.

Auth: reads use ``require_fleet_peer`` (hub/peer readable, same as the fleet
routes); mutations use ``require_orchestrator_peer`` so Barb/Orchestrator can
call with the fleet bearer token and a local Conductor can call over loopback.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from identity import require_fleet_peer, require_orchestrator_peer
from pydantic import BaseModel, Field, field_validator
from staff.adapters import available_providers
from staff.runner import RunRequest, get_runner
from staff.store import ACTIVE_STATUSES, RUN_STATUSES

log = logging.getLogger("dashboard.staff")
router = APIRouter(prefix="/api/staff", tags=["staff"])

MAX_LIMIT = 500
STREAM_POLL_SECONDS = 0.5
STREAM_IDLE_TIMEOUT_SECONDS = 6 * 3600


class RunBody(BaseModel):
    """POST body for a dispatch. DbC: exactly the fields the runner understands."""

    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=80)
    repo: str = Field(default="", max_length=120)
    issue: int | None = Field(default=None, ge=1)
    pr: int | None = Field(default=None, ge=1)
    prompt: str = Field(default="", max_length=20000)
    machine: str = Field(default="local", max_length=60)
    dry_run: bool = False

    @field_validator("repo")
    @classmethod
    def _bare_repo(cls, value: str) -> str:
        value = value.strip()
        if "/" in value or ".." in value:
            raise ValueError("repo must be a bare repository name (no owner, no path)")
        return value


def _today_iso() -> str:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


@router.get("/roster")
async def roster(_peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    runner = get_runner()
    roles = runner.roles()
    active = runner.store.active_runs()
    per_role: dict[str, int] = {}
    for run in active:
        per_role[run.role] = per_role.get(run.role, 0) + 1
    return {
        "machine": runner.machine,
        "roles": [{**spec.to_dict(), "active_runs": per_role.get(name, 0)} for name, spec in sorted(roles.items())],
        "providers": available_providers(),
        "active_runs": len(active),
    }


@router.get("/board")
async def board(_peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    """Local status monitor. Hub aggregation across nodes is issue #1195."""
    runner = get_runner()
    store = runner.store
    active = store.active_runs()
    recent = store.list_runs(limit=20)
    return {
        "machine": runner.machine,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "running": [r.to_dict() for r in active if r.status == "running"],
        "queued": [r.to_dict() for r in active if r.status in ("queued", "preparing")],
        "recent": [r.to_dict() for r in recent if r.status not in ACTIVE_STATUSES],
        "spend_today_usd": store.spend_since(_today_iso()),
        "providers": available_providers(),
    }


@router.get("/runs")
async def list_runs(
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    role: str | None = Query(default=None, max_length=60),
    status: str | None = Query(default=None, max_length=20),
    since: str | None = Query(default=None, max_length=40),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    if status is not None and status not in RUN_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(RUN_STATUSES)}")
    runs = get_runner().store.list_runs(limit=limit, role=role, status=status, since=since)
    return {"runs": [r.to_dict() for r in runs], "count": len(runs)}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    events: int = Query(default=200, ge=0, le=MAX_LIMIT),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    store = get_runner().store
    rec = store.get_run(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run not found")
    evs = store.events_after(run_id, 0, limit=MAX_LIMIT)
    return {"run": rec.to_dict(), "events": evs[-events:] if events else []}


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    after: int = Query(default=0, ge=0),
    _peer: str = Depends(require_fleet_peer),
) -> StreamingResponse:
    """SSE feed of run events. Ends with an ``end`` event when the run finishes."""
    store = get_runner().store
    if store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")

    async def _gen():  # type: ignore[no-untyped-def]
        cursor = after
        idle = 0.0
        while True:
            batch = store.events_after(run_id, cursor, limit=200)
            for ev in batch:
                cursor = int(ev["seq"])
                yield f"id: {cursor}\nevent: {ev['kind']}\ndata: {json.dumps(ev)}\n\n"
            rec = store.get_run(run_id)
            if rec is None or rec.status not in ACTIVE_STATUSES:
                payload = rec.to_dict() if rec else {"id": run_id, "status": "missing"}
                yield f"event: end\ndata: {json.dumps(payload)}\n\n"
                return
            if not batch:
                idle += STREAM_POLL_SECONDS
                if idle >= STREAM_IDLE_TIMEOUT_SECONDS:
                    yield 'event: end\ndata: {"reason": "idle timeout"}\n\n'
                    return
                if int(idle) % 15 == 0 and idle > 0:
                    yield ": keep-alive\n\n"
            else:
                idle = 0.0
            await asyncio.sleep(STREAM_POLL_SECONDS)

    return StreamingResponse(
        _gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, caller: str = Depends(require_orchestrator_peer)) -> dict[str, Any]:
    runner = get_runner()
    if runner.store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    ok = runner.cancel(run_id)
    log.info("staff: cancel %s by %s → %s", run_id, caller, ok)
    rec = runner.store.get_run(run_id)
    return {"cancelled": ok, "run": rec.to_dict() if rec else None}


@router.post("/{role}/run")
async def dispatch(role: str, body: RunBody, caller: str = Depends(require_orchestrator_peer)) -> dict[str, Any]:
    """Dispatch a staff run on this node, or preview it with ``dry_run``.

    Precondition: the role exists and is dispatchable; the provider is allowed
    for the role; one of issue/pr/prompt is given. Postcondition: on a real
    dispatch a ``queued`` run row exists before the response is returned.
    """
    runner = get_runner()
    req = RunRequest(
        role=role,
        provider=body.provider,
        model=body.model,
        repo=body.repo,
        issue=body.issue,
        pr=body.pr,
        prompt=body.prompt,
        machine=body.machine,
        requested_by=caller,
    )
    try:
        plan = runner.plan(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.dry_run:
        return {"dry_run": True, "plan": plan.to_dict(), "machine": runner.machine}
    rec = runner.submit(req)
    log.info(
        "staff: dispatched %s role=%s provider=%s repo=%s target=%s by %s",
        rec.id,
        rec.role,
        rec.provider,
        rec.repo,
        rec.target_ref,
        caller,
    )
    return {"dry_run": False, "run": rec.to_dict(), "machine": runner.machine}
