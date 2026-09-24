"""Staff Hub API (epic #1192, issues #1194 / #1195 / #1197).

Routes (all under ``/api/staff``):
  GET  /roster                Roster: roles, provider availability, active counts.
  GET  /board                 Status monitor: fleet-wide when peers exist, ``?local=1`` for this node.
  GET  /summary               One-call brief for Barb / Orchestrator (in flight, attention, spend, holds).
  GET  /runs                  Run history (filters: role, status, since, limit).
  GET  /runs/{id}             One run + its most recent events.
  GET  /runs/{id}/stream      Server-sent events: live output until the run ends.
  POST /{role}/run            Dispatch here, forward to ``machine`` or place with ``machine: auto``;
                              ``dry_run`` previews the plan.
  POST /runs/{id}/cancel      Terminate a run.

Auth: reads use ``require_fleet_peer`` (hub/peer readable, same as the fleet
routes); mutations use ``require_orchestrator_peer`` so Barb/Orchestrator can
call with the fleet bearer token and a local Conductor can call over loopback.
"""

# ruff: noqa: B008
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, ConfigDict, Field, field_validator
from staff import consolidation
from staff import fleet as staff_fleet
from staff import liveness as staff_liveness
from staff.adapters import available_providers
from staff.audit import export_audit_csv, export_audit_ndjson, get_audit_store, record_audit
from staff.rm_sync import source_status
from staff.runner import RunRequest, StaffRunner, get_runner
from staff.store import ACTIVE_STATUSES, RUN_STATUSES

log = logging.getLogger("dashboard.staff")
router = APIRouter(prefix="/api/staff", tags=["staff"])

MAX_LIMIT = 500
STREAM_POLL_SECONDS = 0.5
STREAM_IDLE_TIMEOUT_SECONDS = 6 * 3600


class StaffBoardResponse(BaseModel):
    """Response model for /api/staff/board (issue #1289)."""

    generated_at: str = Field(description="ISO-8601 UTC timestamp")
    machine: str | None = Field(default=None, description="Local machine hostname")
    hub: str | None = Field(default=None, description="Hub machine hostname when aggregated")
    running: list[dict[str, Any]] = Field(default_factory=list)
    queued: list[dict[str, Any]] = Field(default_factory=list)
    recent: list[dict[str, Any]] = Field(default_factory=list)
    spend_today_usd: dict[str, float] = Field(
        default_factory=dict,
        description="Per-provider spend in USD plus a 'total' key (issue #1289)",
    )
    providers: dict[str, Any] = Field(default_factory=dict)
    liveness: list[dict[str, Any]] = Field(default_factory=list)
    liveness_alerts: list[dict[str, Any]] = Field(default_factory=list)
    machines: dict[str, dict[str, Any]] | None = Field(default=None)
    online: list[str] = Field(default_factory=list)
    offline: list[str] = Field(default_factory=list)
    rm_source: dict[str, Any] | None = Field(default=None)

    model_config = ConfigDict(extra="allow")


class StaffSummaryResponse(BaseModel):
    """Response model for /api/staff/summary (issue #1289)."""

    generated_at: str = Field(description="ISO-8601 UTC timestamp")
    hub: str = Field(description="Hub machine hostname")
    machines_online: list[str] = Field(default_factory=list)
    machines_offline: list[str] = Field(default_factory=list)
    in_flight: list[dict[str, Any]] = Field(default_factory=list)
    recent_24h: dict[str, int] = Field(default_factory=dict)
    attention: list[dict[str, Any]] = Field(default_factory=list)
    spend_today_usd: dict[str, float] = Field(
        default_factory=dict,
        description="Per-provider spend in USD plus a 'total' key (issue #1289)",
    )
    providers: dict[str, Any] = Field(default_factory=dict)
    holds: list[dict[str, Any]] = Field(default_factory=list)
    liveness_alerts: list[dict[str, Any]] = Field(default_factory=list)
    roles: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


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
@router.get("/roles")
async def roster(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
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


def _local_board(runner: StaffRunner) -> dict[str, Any]:
    store = runner.store
    active = store.active_runs()
    recent = store.list_runs(limit=20)
    now = datetime.now(UTC)
    liveness = staff_liveness.compute_liveness(runner.roles(), store, staff_liveness.load_scheduler_state(), now)
    staff_liveness.notify_dead(liveness, runner.machine)
    return {
        "machine": runner.machine,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "running": [r.to_dict() for r in active if r.status == "running"],
        "queued": [r.to_dict() for r in active if r.status in ("queued", "preparing")],
        "recent": [r.to_dict() for r in recent if r.status not in ACTIVE_STATUSES],
        "spend_today_usd": store.spend_since(_today_iso()),
        "providers": available_providers(),
        "liveness": liveness,
        "rm_source": source_status(),
    }


@router.get("/board", response_model=StaffBoardResponse, response_model_exclude_none=True)
async def board(
    local: bool = Query(
        default=False,
        description="Return only this node's board (used by hub fan-out).",
    ),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Status monitor. With peers configured this is the fleet-wide view (#1195)."""
    runner = get_runner()
    mine = _local_board(runner)
    peers = {} if local else staff_fleet.peer_nodes()
    if not peers:
        return mine
    return await staff_fleet.aggregate_board(mine, peers)


def _holds_snapshot() -> list[dict[str, Any]]:
    """Active holds when the scheduler module (#1196) is present; empty otherwise."""
    try:
        from staff import holds as staff_holds  # noqa: PLC0415
    except ImportError:
        return []
    loader = getattr(staff_holds, "active_holds", None)
    if loader is None:
        return []
    try:
        return [dict(h) for h in loader()]
    except Exception as exc:  # noqa: BLE001
        log.warning("staff: holds unavailable: %s", exc)
        return []


@router.get("/summary", response_model=StaffSummaryResponse, response_model_exclude_none=True)
async def summary(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """The one-call brief for Barb and Orchestrator (#1195).

    Flat payload: what is in flight fleet-wide, what needs attention (failed or
    blocked in the last 24 h on this node), spend today, provider availability
    per machine, active holds, late/dead scheduled roles (#1209) and the
    roster with schedules.
    """
    runner = get_runner()
    board_view = await staff_fleet.aggregate_board(_local_board(runner))
    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    recent = runner.store.list_runs(limit=200, since=since)
    counts: dict[str, int] = {}
    for run in recent:
        counts[run.status] = counts.get(run.status, 0) + 1
    attention = [
        {
            "id": r.id,
            "role": r.role,
            "repo": r.repo,
            "target_ref": r.target_ref,
            "status": r.status,
            "error": r.error,
            "failure_class": getattr(r, "failure_class", ""),
        }
        for r in recent
        if r.status in ("failed", "blocked")
    ]
    keep = (
        "id",
        "role",
        "provider",
        "machine",
        "repo",
        "target_ref",
        "status",
        "started_at",
        "last_line",
    )
    in_flight = [{k: r.get(k) for k in keep} for r in [*board_view["running"], *board_view["queued"]]]
    roles = runner.roles()
    return {
        "generated_at": board_view["generated_at"],
        "hub": board_view["hub"],
        "machines_online": board_view["online"],
        "machines_offline": board_view["offline"],
        "in_flight": in_flight,
        "recent_24h": counts,
        "attention": attention[:20],
        "spend_today_usd": board_view["spend_today_usd"],
        "providers": board_view["providers"],
        "holds": _holds_snapshot(),
        "liveness_alerts": board_view.get("liveness_alerts", []),
        "roles": [
            {
                "name": s.name,
                "title": s.title,
                "schedule": s.schedule,
                "surface": s.surface,
                "retired": s.retired,
            }
            for s in sorted(roles.values(), key=lambda s: s.name)
        ],
    }


@router.get("/audit")
async def list_audit(
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    principal: str | None = Query(default=None, max_length=120),
    thread_id: str | None = Query(default=None, max_length=120),
    run_id: str | None = Query(default=None, max_length=120),
    action: str | None = Query(default=None, max_length=60),
    surface: str | None = Query(default=None, max_length=30),
    target: str | None = Query(default=None, max_length=120),
    since: str | None = Query(default=None, max_length=40),
    format: str | None = Query(default=None),
    _peer: Principal = Depends(require_scope("staff.audit.read")),
) -> Any:
    """Retrieve durable append-only staff audit rows (SC-A8, Issue #1298)."""
    store = get_audit_store()
    filt = {
        k: v
        for k, v in [
            ("principal", principal),
            ("thread_id", thread_id),
            ("run_id", run_id),
            ("action", action),
            ("surface", surface),
            ("target", target),
            ("since", since),
        ]
    }
    entries = store.list_entries(limit=limit, offset=offset, **filt)
    if format == "csv":
        headers = {"Content-Disposition": "attachment; filename=staff_audit.csv"}
        return Response(content=export_audit_csv(entries), media_type="text/csv", headers=headers)
    if format == "ndjson":
        return Response(content=export_audit_ndjson(entries), media_type="application/x-ndjson")
    return {"entries": [e.to_dict() for e in entries], "count": len(entries), "total": store.count_entries(**filt)}


@router.get("/runs")
async def list_runs(
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    role: str | None = Query(default=None, max_length=60),
    status: str | None = Query(default=None, max_length=20),
    since: str | None = Query(default=None, max_length=40),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    if status is not None and status not in RUN_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(RUN_STATUSES)}")
    runs = get_runner().store.list_runs(limit=limit, role=role, status=status, since=since)
    return {"runs": [r.to_dict() for r in runs], "count": len(runs)}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    events: int = Query(default=200, ge=0, le=MAX_LIMIT),
    _peer: Principal = Depends(require_scope("staff.read")),
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
    _peer: Principal = Depends(require_scope("staff.read")),
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
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, caller: Principal = Depends(require_scope("staff.cancel"))) -> dict[str, Any]:
    runner = get_runner()
    if runner.store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    ok = runner.cancel(run_id)
    caller_str = format_caller(caller)
    record_audit(
        action="cancel",
        target=f"run:{run_id}",
        principal=caller_str,
        surface="api",
        run_id=run_id,
        outcome="success" if ok else "not_found",
        detail={"cancelled": ok},
        fail_closed=True,
    )
    log.info("staff: cancel %s by %s → %s", run_id, caller_str, ok)
    rec = runner.store.get_run(run_id)
    return {"cancelled": ok, "run": rec.to_dict() if rec else None}


async def _resolve_target(runner: StaffRunner, machine: str, provider: str) -> str:
    """Machine targeting (#1197): ``local`` / this host → local; ``auto`` → least loaded
    online node with the provider installed; a peer name → that peer; unknown → 422."""
    peers = staff_fleet.peer_nodes()
    if machine.strip().lower() == "auto":
        board_view = await staff_fleet.aggregate_board(_local_board(runner), peers)
        chosen = staff_fleet.choose_machine(board_view, provider, runner.machine)
        return "local" if chosen == runner.machine else chosen
    resolved = staff_fleet.resolve_machine(machine, runner.machine, peers)
    if resolved is None:
        known = ", ".join([runner.machine, *sorted(peers)]) or runner.machine
        raise HTTPException(
            status_code=422,
            detail=f"unknown machine '{machine}' (known: {known}, or 'auto')",
        )
    return resolved


async def _forward(target: str, role: str, body: RunBody, caller: str) -> dict[str, Any]:
    url = staff_fleet.peer_nodes().get(target)
    if url is None:
        raise HTTPException(status_code=422, detail=f"unknown machine '{target}'")
    try:
        status, data = await staff_fleet.forward_run(url, role, body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"machine '{target}' unreachable: {exc}") from exc
    if status >= 400:
        raise HTTPException(status_code=status if status < 500 else 502, detail=data.get("detail", data))
    log.info(
        "staff: forwarded %s run for role=%s to %s by %s",
        "dry-run" if body.dry_run else "dispatch",
        role,
        target,
        caller,
    )
    return {**data, "machine": data.get("machine", target), "forwarded_to": target}


@router.post("/{role}/run")
async def dispatch(
    role: str,
    body: RunBody,
    caller: Principal = Depends(require_scope("staff.dispatch")),
) -> dict[str, Any]:
    """Dispatch a staff run on this node, or preview it with ``dry_run``.

    Precondition: the role exists and is dispatchable; the provider is allowed
    for the role; one of issue/pr/prompt is given. Postcondition: on a real
    dispatch a ``queued`` run row exists before the response is returned. When
    the role carries ``strategy.consolidate_when`` and a repo is given, the
    PR-consolidation decision (#1213) is evaluated first and lands in
    ``plan.consolidation`` and the run's ``strategy_mode``.
    """
    runner = get_runner()
    spec = runner.roles().get(role)
    decision = None
    if spec is not None and body.repo and consolidation.threshold(spec) is not None:
        decision = await asyncio.to_thread(consolidation.decide, spec, body.repo)  # #1213: gh + capacity I/O
    caller_str = format_caller(caller)
    req = RunRequest(
        role=role,
        provider=body.provider,
        model=body.model,
        repo=body.repo,
        issue=body.issue,
        pr=body.pr,
        prompt=body.prompt,
        machine=body.machine,
        requested_by=caller_str,
        consolidation=decision,
    )
    try:
        plan = runner.plan(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    target = await _resolve_target(runner, body.machine, plan.provider)
    if target != "local":
        return await _forward(target, role, body, caller_str)
    if body.dry_run:
        return {"dry_run": True, "plan": plan.to_dict(), "machine": runner.machine}
    rec = runner.submit(req)
    record_audit(
        action="dispatch",
        target=f"role:{rec.role}",
        principal=caller_str,
        surface="api",
        request_id=rec.id,
        run_id=rec.id,
        outcome="success",
        detail={
            "repo": rec.repo,
            "target_ref": rec.target_ref,
            "provider": rec.provider,
            "machine": rec.machine,
        },
        fail_closed=True,
    )
    log.info(
        "staff: dispatched %s role=%s provider=%s repo=%s target=%s by %s",
        rec.id,
        rec.role,
        rec.provider,
        rec.repo,
        rec.target_ref,
        caller_str,
    )
    return {"dry_run": False, "run": rec.to_dict(), "machine": runner.machine}
