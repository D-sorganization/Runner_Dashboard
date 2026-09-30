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
from routers.staff_schedule import HoldsBody, current_holds, replace_holds, schedule_snapshot
from staff import fleet as staff_fleet
from staff import summary_view, usage
from staff.adapters import ADAPTERS, available_providers, chat_only_providers, unattended_providers
from staff.audit import (
    get_audit_store,
    record_audit,
)
from staff.cli_version import provider_versions
from staff.idempotency import (
    IdempotencyStoreError,
    get_idempotency_store,
    payload_fingerprint,
    require_idempotency_header,
)
from staff.models import (
    StaffBoardResponse,
    StaffCancelResponse,
    StaffDispatchResponse,
    StaffHoldsResponse,
    StaffPricingResponse,
    StaffProvidersResponse,
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
from staff.store import USAGE_GROUPS

log = logging.getLogger("dashboard.staff.v1")
router = APIRouter(prefix="/api/v1/staff", tags=["staff-v1"])
router.include_router(staff_knowledge_router)


def _reservation_conflict(state: str, operation_id: str) -> HTTPException:
    """The 409 for a reservation this request cannot run under (#1795)."""
    if state == "mismatch":
        detail = {
            "code": "idempotency_key_reused",
            "message": "This Idempotency-Key was already used with a different request body.",
            "retryable": False,
            "hint": "Use a new Idempotency-Key for a different request.",
        }
        return HTTPException(status_code=409, detail=detail)
    if state == "in_progress":
        detail = {
            "code": "idempotency_in_progress",
            "message": "A request with this Idempotency-Key is still being processed.",
            "retryable": True,
            "hint": "Retry the same request shortly to receive its result.",
        }
        return HTTPException(status_code=409, detail=detail, headers={"Retry-After": "1"})
    detail = {
        "code": "idempotency_outcome_unknown",
        "message": (
            f"An earlier request with this Idempotency-Key (operation {operation_id}) never recorded its result."
        ),
        "retryable": False,
        "hint": "Check GET /api/v1/staff/runs before sending a new request with a new key.",
        "operation_id": operation_id,
    }
    return HTTPException(status_code=409, detail=detail)


async def _handle_idempotent_post(
    key: str,
    endpoint: str,
    caller: str,
    payload: Any,
    action: Any,
    *,
    takeover_safe: bool = False,
) -> Response:
    """Execute ``action`` at most once per (key, endpoint, caller) for 24 h.

    The key is reserved before the action runs, so concurrent requests with the same
    key execute once (#1795). A store failure before the action fails closed with 503.
    ``takeover_safe`` marks actions that may be re-run after a lapsed reservation.
    """
    store = get_idempotency_store()
    try:
        reservation = store.reserve(key, endpoint, caller, payload_fingerprint(payload), takeover_safe=takeover_safe)
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

    if reservation.state == "replay" and reservation.record is not None:
        cached = reservation.record
        headers = dict(cached.response_headers)
        headers["Idempotent-Replay"] = "true"
        return Response(
            content=cached.response_body,
            status_code=cached.status_code,
            media_type="application/json",
            headers=headers,
        )
    if reservation.state != "acquired":
        raise _reservation_conflict(reservation.state, reservation.operation_id)

    try:
        res = action()
        result = await res if asyncio.iscoroutine(res) else res
    except BaseException:
        # The action did not complete, so free the key for a retry.
        try:
            store.release(key, endpoint, caller)
        except IdempotencyStoreError:
            log.exception("could not release idempotency key %s for %s", key, endpoint)
        raise

    status_code = 200
    if isinstance(result, Response):
        body_json = bytes(result.body).decode("utf-8")
        status_code = result.status_code
    elif isinstance(result, BaseModel):
        body_json = json.dumps(result.model_dump())
    elif isinstance(result, dict):
        body_json = json.dumps(result)
    else:
        body_json = json.dumps({"result": result})
    resp_headers = {"Content-Type": "application/json"}

    try:
        store.complete(key, endpoint, caller, status_code, resp_headers, body_json)
    except IdempotencyStoreError:
        # The effect happened; report it. The reservation stays pending, so a retry is
        # answered with in-progress or unknown-outcome instead of running again.
        log.exception("could not record idempotent result for %s under key %s", endpoint, key)
        resp_headers["Idempotency-Receipt"] = "unrecorded"

    return Response(
        content=body_json,
        status_code=status_code,
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
        "provider_versions": await asyncio.to_thread(provider_versions, ADAPTERS),
        "chat_only_providers": chat_only_providers(),
        "active_runs": len(active),
    }


@router.get("/providers", response_model=StaffProvidersResponse, response_model_exclude_none=True)
async def providers_v1(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Provider availability, chat-only/unattended capabilities, and CLI versions (#1697)."""
    return {
        "providers": available_providers(),
        "chat_only_providers": chat_only_providers(),
        "unattended_providers": unattended_providers(),
        "provider_versions": await asyncio.to_thread(provider_versions, ADAPTERS),
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
    """The one-call staff brief, identical to ``GET /api/staff/summary`` (#1195).

    One builder serves both routes, so the versioned API cannot drift from the
    payload Barb and the staff runs read.
    """
    return await summary_view.build_staff_summary()


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
    # Delegate to the legacy handler so both routes share one read path (and v1 gains its
    # remote-run proxy); only the 404 is reshaped into the v1 error envelope.
    from routers.staff import get_run as staff_get_run

    try:
        return await staff_get_run(run_id=run_id, events=200, _peer=_peer)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": f"Run {run_id} not found",
                "retryable": False,
                "hint": "Check the run ID or list runs via GET /api/v1/staff/runs.",
            },
        ) from exc


@router.get("/runs/{run_id}/stream")
async def stream_run_v1(
    run_id: str,
    after: int = Query(default=0, ge=0),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> StreamingResponse:
    from routers.staff import stream_run

    return await stream_run(run_id=run_id, after=after, _peer=_peer)


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

    # A lapsed dispatch reservation is not retaken: re-running it could start a second run.
    payload = body.model_dump(mode="json")
    return await _handle_idempotent_post(idempotency_key, endpoint, caller, payload, _execute)


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

    return await _handle_idempotent_post(idempotency_key, endpoint, caller, {}, _execute, takeover_safe=True)


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
    records = store.list_entries(limit=500, principal=principal, action=action)
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
    return schedule_snapshot()


@router.get("/holds", response_model=StaffHoldsResponse)
async def get_holds_v1(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    return {"holds": current_holds()}


@router.put("/holds", response_model=StaffHoldsResponse)
async def put_holds_v1(
    body: HoldsBody,
    idempotency_key: str = Depends(require_idempotency_header),
    _peer: Principal = Depends(require_scope("staff.holds.write")),
) -> Response:
    caller = format_caller(_peer)
    endpoint = "PUT /api/v1/staff/holds"

    def _execute() -> dict[str, Any]:
        updated = replace_holds(body)
        record_audit(
            principal=caller,
            action="hold_set",
            surface="api_v1",
            target="holds",
            outcome="succeeded",
            detail={"count": len(updated)},
        )
        return {"holds": updated}

    payload = body.model_dump(mode="json")
    return await _handle_idempotent_post(idempotency_key, endpoint, caller, payload, _execute, takeover_safe=True)


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

    return await _handle_idempotent_post(idempotency_key, endpoint, caller_str, {}, _execute, takeover_safe=True)
