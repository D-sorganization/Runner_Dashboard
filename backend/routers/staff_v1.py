"""Versioned Public Staff API v1 (/api/v1/staff) (SC-F3, Issue #1312).

Specifications:
- Public stable routes under /api/v1/staff
- Error envelope on all 4xx/5xx responses
- Mandatory Idempotency-Key on mutating POST routes with 24h replay
- Cursor pagination on list endpoints (runs, audit, threads)
- Scoped authentication via identity.require_scope
"""

# ruff: noqa: B008
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel
from routers.staff import (
    MAX_LIMIT,
    RunBody,
)
from routers.staff_knowledge import router as staff_knowledge_router
from routers.staff_schedule import HoldsBody
from staff import fleet as staff_fleet
from staff import usage
from staff.adapters import available_providers
from staff.audit import (
    get_audit_store,
    record_audit,
)
from staff.classifier import format_attention_items
from staff.idempotency import (
    IdempotencyStoreError,
    get_idempotency_store,
    require_idempotency_header,
)
from staff.models import (
    StaffBoardResponse,
    StaffCancelResponse,
    StaffDispatchResponse,
    StaffHoldsResponse,
    StaffPricingResponse,
    StaffRosterResponse,
    StaffRunDetailResponse,
    StaffScheduleResponse,
    StaffSummaryResponse,
    StaffUsageExportResponse,
    StaffUsageResponse,
)
from staff.pagination import CursorPage, paginate_items
from staff.pricing import price_table_rows
from staff.runner import get_runner
from staff.scheduler import get_scheduler
from staff.store import USAGE_GROUPS

log = logging.getLogger("dashboard.staff.v1")
router = APIRouter(prefix="/api/v1/staff", tags=["staff-v1"])
router.include_router(staff_knowledge_router)


async def _handle_idempotent_post(
    key: str,
    endpoint: str,
    caller: str,
    action: Any,
) -> Response:
    """Execute action with 24h idempotency guarantee, failing closed (503) on store error."""
    store = get_idempotency_store()
    try:
        cached = store.get(key=key, endpoint=endpoint, principal=caller)
    except IdempotencyStoreError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "idempotency_store_failure",
                "message": f"Idempotency store lookup failed: {exc}",
                "retryable": True,
                "hint": "Retry after the database connection is restored; request aborted to prevent duplicates.",
            },
        ) from exc

    if cached is not None:
        headers = dict(cached.response_headers)
        headers["Idempotent-Replay"] = "true"
        return Response(
            content=cached.response_body,
            status_code=cached.status_code,
            media_type="application/json",
            headers=headers,
        )

    res = action()
    if asyncio.iscoroutine(res):
        result = await res
    else:
        result = res

    if isinstance(result, BaseModel):
        body_dict = result.model_dump()
    elif isinstance(result, dict):
        body_dict = result
    elif isinstance(result, Response):
        return result
    else:
        body_dict = {"result": result}

    body_json = json.dumps(body_dict)
    resp_headers = {"Content-Type": "application/json"}

    try:
        store.save(
            key=key,
            endpoint=endpoint,
            principal=caller,
            status_code=200,
            response_headers=resp_headers,
            response_body=body_json,
        )
    except IdempotencyStoreError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "idempotency_store_failure",
                "message": f"Idempotency store record failed: {exc}",
                "retryable": True,
                "hint": (
                    "Action was completed but could not be recorded in idempotency ledger; "
                    "subsequent retries may re-execute."
                ),
            },
        ) from exc

    return Response(
        content=body_json,
        status_code=200,
        media_type="application/json",
        headers=resp_headers,
    )


# ── Roster & Board ─────────────────────────────────────────────────────────────


@router.get("/roster", response_model=StaffRosterResponse, response_model_exclude_none=True)
@router.get("/roles", response_model=StaffRosterResponse, response_model_exclude_none=True)
async def roster_v1(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    runner = get_runner()
    roles, active = runner.roles(), runner.store.active_runs()
    per_role = {s: sum(1 for a in active if a.role == s) for s in roles}
    return {
        "machine": runner.machine,
        "roles": [{**spec.to_dict(), "active_runs": per_role.get(name, 0)} for name, spec in sorted(roles.items())],
        "providers": available_providers(),
        "active_runs": len(active),
    }


@router.get("/board", response_model=StaffBoardResponse, response_model_exclude_none=True)
async def board_v1(
    local: bool = Query(default=False, description="Return only this node's board"),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    runner = get_runner()
    mine = staff_fleet.local_board(runner)
    peers = {} if local else staff_fleet.peer_nodes()
    if not peers:
        return mine
    return await staff_fleet.aggregate_board(mine, peers)


@router.get("/summary", response_model=StaffSummaryResponse, response_model_exclude_none=True)
async def summary_v1(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    runner = get_runner()
    board_view = await staff_fleet.aggregate_board(staff_fleet.local_board(runner))
    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    recent = runner.store.list_runs(limit=200, since=since)
    counts: dict[str, int] = {}
    for run in recent:
        counts[run.status] = counts.get(run.status, 0) + 1
    attention = format_attention_items(recent)
    keep = (
        "id",
        "role",
        "machine",
        "status",
        "target_kind",
        "target_ref",
        "created_at",
        "started_at",
        "ended_at",
        "error",
    )
    recent_runs = [{k: getattr(r, k) for k in keep} for r in recent[:20]]
    scheduler = get_scheduler()
    sched_view = scheduler.schedule_view(runner.roles())
    return {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "machine": runner.machine,
        "in_flight": board_view.get("running_runs", 0),
        "queued": board_view.get("queued_runs", 0),
        "counts_24h": counts,
        "spend_today_usd": board_view.get("spend_today_usd", 0.0),
        "recent_runs": recent_runs,
        "attention": attention,
        "holds": sched_view.get("holds", []),
        "schedule": sched_view.get("schedule", []),
        "liveness_alerts": board_view.get("liveness_alerts", []),
    }


# ── Runs & Pagination ──────────────────────────────────────────────────────────


class StaffV1RunsPage(CursorPage[dict[str, Any]]):
    runs: list[dict[str, Any]] = []
    count: int = 0


@router.get("/runs", response_model=StaffV1RunsPage)
async def list_runs_v1(
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    cursor: str | None = Query(default=None, description="Cursor for keyset pagination"),
    role: str | None = Query(default=None),
    status: str | None = Query(default=None),
    since: str | None = Query(default=None),
    logical_only: bool = Query(default=True),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> StaffV1RunsPage:
    runner = get_runner()
    runs = runner.store.list_runs(
        limit=MAX_LIMIT,
        role=role,
        status=status,
        since=since,
        logical_only=logical_only,
    )
    raw_dicts = [r.to_dict() for r in runs]
    page = paginate_items(raw_dicts, limit=limit, cursor=cursor, key_fn=lambda r: (r["created_at"], r["id"]))
    return StaffV1RunsPage(
        items=page.items,
        runs=page.items,
        count=len(page.items),
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
        has_more=page.has_more,
    )


@router.get("/runs/{run_id}", response_model=StaffRunDetailResponse, response_model_exclude_none=True)
async def get_run_v1(
    run_id: str,
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    runner = get_runner()
    run = runner.store.get_run(run_id)
    if not run:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": f"Run {run_id} not found",
                "retryable": False,
                "hint": "Check the run ID or list runs via GET /api/v1/staff/runs.",
            },
        )
    events = [e.to_dict() for e in runner.store.list_events(run_id, limit=200)]
    attempts = [a.to_dict() for a in runner.store.get_attempts(run_id)]
    return {"run": run.to_dict(), "events": events, "attempts": attempts}


@router.get("/runs/{run_id}/stream")
async def stream_run_v1(
    run_id: str,
    after: int = Query(default=0, ge=0),
    request: Request = None,  # type: ignore
    _peer: Principal = Depends(require_scope("staff.read")),
) -> StreamingResponse:
    from routers.staff import stream_run

    return await stream_run(run_id=run_id, after=after, request=request, _peer=_peer)


# ── Mutating Operations (Idempotent) ──────────────────────────────────────────


@router.post("/{role}/run", response_model=StaffDispatchResponse)
async def dispatch_v1(
    role: str,
    body: RunBody,
    request: Request,
    idempotency_key: str = Depends(require_idempotency_header),
    _peer: Principal = Depends(require_scope("staff.dispatch")),
) -> Response:
    caller = format_caller(_peer)
    endpoint = f"POST /api/v1/staff/{role}/run"

    async def _execute() -> Any:
        from routers.staff import dispatch as staff_dispatch

        return await staff_dispatch(role=role, body=body, request=request, caller=_peer)

    return await _handle_idempotent_post(idempotency_key, endpoint, caller, _execute)


@router.post("/runs/{run_id}/cancel", response_model=StaffCancelResponse)
async def cancel_run_v1(
    run_id: str,
    idempotency_key: str = Depends(require_idempotency_header),
    _peer: Principal = Depends(require_scope("staff.cancel")),
) -> Response:
    caller = format_caller(_peer)
    endpoint = f"POST /api/v1/staff/runs/{run_id}/cancel"

    async def _execute() -> Any:
        from routers.staff import cancel_run as staff_cancel_run

        return await staff_cancel_run(run_id=run_id, caller=_peer)

    return await _handle_idempotent_post(idempotency_key, endpoint, caller, _execute)


# ── Audit, Holds, Schedule & Usage ────────────────────────────────────────────


class StaffV1AuditPage(CursorPage[dict[str, Any]]):
    entries: list[dict[str, Any]] = []
    count: int = 0


@router.get("/audit", response_model=StaffV1AuditPage)
async def list_audit_v1(
    limit: int = Query(default=50, ge=1, le=500),
    cursor: str | None = Query(default=None),
    principal: str | None = Query(default=None),
    action: str | None = Query(default=None),
    _peer: Principal = Depends(require_scope("staff.audit.read")),
) -> StaffV1AuditPage:
    store = get_audit_store()
    records = store.query(limit=500, principal=principal, action=action)
    raw = [r.to_dict() for r in records]
    page = paginate_items(raw, limit=limit, cursor=cursor, key_fn=lambda r: (r["ts"], r["id"]))
    return StaffV1AuditPage(
        items=page.items,
        entries=page.items,
        count=len(page.items),
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
        has_more=page.has_more,
    )


@router.get("/schedule", response_model=StaffScheduleResponse)
async def get_schedule_v1(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    scheduler = get_scheduler()
    runner = get_runner()
    return scheduler.schedule_view(runner.roles())


@router.get("/holds", response_model=StaffHoldsResponse)
async def get_holds_v1(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    scheduler = get_scheduler()
    return {"holds": [h.to_dict() for h in scheduler.holds.list()]}


@router.put("/holds", response_model=StaffHoldsResponse)
async def put_holds_v1(
    body: HoldsBody,
    idempotency_key: str = Depends(require_idempotency_header),
    _peer: Principal = Depends(require_scope("staff.holds.write")),
) -> Response:
    caller = format_caller(_peer)
    endpoint = "PUT /api/v1/staff/holds"

    def _execute() -> dict[str, Any]:
        scheduler = get_scheduler()
        raw_holds = [h.model_dump() for h in body.holds]
        updated = scheduler.replace_holds(raw_holds)
        record_audit(
            principal=caller,
            action="hold_set",
            surface="api_v1",
            target="holds",
            outcome="succeeded",
            detail={"count": len(updated)},
        )
        return {"holds": [h.to_dict() for h in updated]}

    return await _handle_idempotent_post(idempotency_key, endpoint, caller, _execute)


@router.get("/usage", response_model=StaffUsageResponse, response_model_exclude_none=True)
async def get_usage_v1(
    since: str | None = Query(default=None, max_length=40),
    group: str = Query(default="provider", max_length=10),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    if group not in USAGE_GROUPS:
        raise HTTPException(status_code=422, detail=f"group must be one of {', '.join(USAGE_GROUPS)}")
    runner = get_runner()
    body = usage.summary(runner.store, group=group, since=since or usage.today_iso())
    body["machine"] = runner.machine
    return body


@router.get("/usage/pricing", response_model=StaffPricingResponse, response_model_exclude_none=True)
async def get_pricing_v1(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    return {"rows": price_table_rows()}


@router.post("/usage/export", response_model=StaffUsageExportResponse, response_model_exclude_none=True)
async def export_usage_v1(
    idempotency_key: str = Depends(require_idempotency_header),
    caller: Principal = Depends(require_scope("staff.admin")),
) -> Response:
    caller_str = format_caller(caller)
    endpoint = "POST /api/v1/staff/usage/export"

    def _execute() -> dict[str, Any]:
        try:
            return usage.export_to_rm(get_runner().store)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return await _handle_idempotent_post(idempotency_key, endpoint, caller_str, _execute)
