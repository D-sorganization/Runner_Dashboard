"""Dispatcher fallback ledger and savings metrics API (#3054)."""

# ruff: noqa: B008

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, ConfigDict, Field
from routers.staff_v1 import _handle_idempotent_post
from staff.dispatcher_metrics import (
    DispatcherRateLimitError,
    aggregate_dispatcher_metrics,
    dispatcher_rows_since,
    record_dispatcher_fallback,
)
from staff.idempotency import require_idempotency_header
from staff.runner import get_runner

router = APIRouter()


class DispatcherFallbackBody(BaseModel):
    """Validated, bounded ledger entry for a dispatcher fallback."""

    reason: Literal["hub_unreachable", "held", "rejected", "no_provider", "small_task"]
    task_class: str = Field(min_length=1, max_length=80)
    est_grok_turns: int = Field(ge=0, le=1000)

    model_config = ConfigDict(extra="forbid")


@router.post("/dispatcher/ledger")
async def write_dispatcher_ledger(
    request: Request,
    body: DispatcherFallbackBody,
    idempotency_key: str = Depends(require_idempotency_header),
    caller: Principal = Depends(require_scope("staff.dispatcher.ledger.write")),
) -> Response:
    """Append one bounded fallback record; the dedicated scope has no read access."""
    if len(await request.body()) > 16_384:
        raise HTTPException(status_code=422, detail="ledger body exceeds 16384 bytes")
    caller_id = format_caller(caller)
    payload = body.model_dump(mode="json")

    def _execute() -> dict[str, Any]:
        now = datetime.now(UTC)
        try:
            fallback = record_dispatcher_fallback(
                get_runner().store.path,
                **payload,
                created_at=now.isoformat().replace("+00:00", "Z"),
                principal=caller_id,
                idempotency_key=idempotency_key,
                rate_window_since=(now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            )
        except DispatcherRateLimitError as exc:
            raise HTTPException(status_code=429, detail="dispatcher ledger rate limit exceeded") from exc
        return {"recorded": True, "fallback": fallback}

    return await _handle_idempotent_post(
        idempotency_key,
        "POST /api/v1/staff/dispatcher/ledger",
        caller_id,
        payload,
        _execute,
    )


@router.get("/dispatcher/metrics")
async def read_dispatcher_metrics(
    days: int = Query(default=7, ge=1, le=90),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Return measured dispatcher counts and visibly labelled estimates."""
    today = datetime.now(UTC).date()
    since_day = today - timedelta(days=days - 1)
    runs, fallbacks = dispatcher_rows_since(get_runner().store.path, f"{since_day.isoformat()}T00:00:00Z")
    return aggregate_dispatcher_metrics(runs, fallbacks, days=days, today=today)
