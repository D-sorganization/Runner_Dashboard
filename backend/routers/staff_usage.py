"""Staff Hub usage ledger, report cards, leaderboard & bean-counter API (/api/staff).

Routes (under ``/api/staff``; the ``/api/staff/`` alt-auth prefix applies):
  GET  /usage           Cost/token/wall-time totals grouped by provider, role or day,
                        plus the daily budget from ``STAFF_BUDGET_USD_PER_DAY``.
  GET  /usage/pricing   The price table this node uses to estimate cost.
  POST /usage/export    Append today's per-provider totals to Repository_Management
                        ``data/credit_usage.json`` (subprocess to the RM script).
  GET  /quota           Live subscription windows per provider (#1587).
  POST /usage/snapshot  Ingest a Usage-Tracker ``provider-availability.v1`` snapshot (#3042).
  GET  /usage/snapshot  Fleet-wide provider usage: per-machine % used and staleness (#3042).
  GET  /report-cards    Zero-LLM-cost Agent Report Cards & Empirical Routing Leaderboard.
  GET  /leaderboard     Alias for /report-cards leaderboard view.
  GET  /bean-counter    Zero-LLM-cost Bean-Counter token economy & waste ledger.
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, format_caller, require_scope
from middleware import WEBHOOK_MAX_BODY_SIZE, limit_body_size
from routers.staff_outcomes import compute_report_cards_payload
from staff import quota, usage, usage_snapshot
from staff.adapters import ADAPTERS
from staff.models import (
    StaffPricingResponse,
    StaffQuotaResponse,
    StaffUsageExportResponse,
    StaffUsageResponse,
    StaffUsageSnapshotIngestResponse,
    StaffUsageSnapshotResponse,
)
from staff.models_insights import StaffBeanCounterResponse, StaffReportCardsResponse
from staff.pricing import price_table_rows
from staff.runner import get_runner
from staff.store import USAGE_GROUPS

log = logging.getLogger("dashboard.staff.usage")
router = APIRouter(prefix="/api/staff", tags=["staff"])


@router.get("/usage", response_model=StaffUsageResponse, response_model_exclude_none=True)
async def get_usage(
    since: str | None = Query(default=None, max_length=40),
    group: str = Query(default="provider", max_length=10),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Usage rows for this node. ``since`` defaults to the start of today (UTC)."""
    if group not in USAGE_GROUPS:
        raise HTTPException(status_code=422, detail=f"group must be one of {', '.join(USAGE_GROUPS)}")
    runner = get_runner()
    body = usage.summary(runner.store, group=group, since=since or usage.today_iso())
    body["machine"] = runner.machine
    return body


@router.get("/quota", response_model=StaffQuotaResponse, response_model_exclude_none=True)
async def get_quota(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Billing kind and current plan windows for every staff provider on this node."""
    return quota.report(ADAPTERS, datetime.now(UTC))


@router.get(
    "/usage/pricing",
    response_model=StaffPricingResponse,
    response_model_exclude_none=True,
)
async def get_pricing(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    return {"rows": price_table_rows()}


@router.post(
    "/usage/export",
    response_model=StaffUsageExportResponse,
    response_model_exclude_none=True,
)
async def export_usage(
    caller: Principal = Depends(require_scope("staff.admin")),
) -> dict[str, Any]:
    """Append today's per-provider totals to RM ``data/credit_usage.json``."""
    try:
        result = usage.export_to_rm(get_runner().store)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    caller_str = format_caller(caller)
    log.info(
        "staff: usage export by %s → %s",
        caller_str,
        "ok" if result["ok"] else "partial failure",
    )
    return result


@router.post(
    "/usage/snapshot",
    response_model=StaffUsageSnapshotIngestResponse,
    response_model_exclude_none=True,
)
@limit_body_size(WEBHOOK_MAX_BODY_SIZE)
async def ingest_usage_snapshot(
    payload: dict[str, Any],
    caller: Principal = Depends(require_scope("staff.admin")),
) -> dict[str, Any]:
    """Ingest a Usage-Tracker ``provider-availability.v1`` snapshot (#3042)."""
    try:
        snapshot = usage_snapshot.validate_payload(payload, now=datetime.now(UTC))
    except usage_snapshot.SnapshotRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    kept = usage_snapshot.default_store().record(snapshot)
    log.info("staff: usage snapshot ingest by %s (kept=%s)", format_caller(caller), kept)
    return {"kept": kept}


@router.get(
    "/usage/snapshot",
    response_model=StaffUsageSnapshotResponse,
    response_model_exclude_none=True,
)
async def get_usage_snapshot(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Fleet-wide provider usage: newest Usage-Tracker snapshot, per-machine staleness (#3042)."""
    return usage_snapshot.view(datetime.now(UTC))


@router.get("/report-cards", response_model=StaffReportCardsResponse)
async def get_staff_report_cards(
    since: str | None = Query(default=None, max_length=40),
    role: str | None = Query(default=None, max_length=64),
    provider: str | None = Query(default=None, max_length=32),
    include_pr_facts: bool = Query(default=False),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Zero-LLM-cost Agent Report Cards, Bean-Counter Ledger & Empirical Routing Leaderboard."""
    return await compute_report_cards_payload(
        since=since,
        role=role,
        provider=provider,
        include_pr_facts=include_pr_facts,
    )


@router.get("/leaderboard", response_model=StaffReportCardsResponse)
async def get_staff_leaderboard(
    since: str | None = Query(default=None, max_length=40),
    role: str | None = Query(default=None, max_length=64),
    provider: str | None = Query(default=None, max_length=32),
    include_pr_facts: bool = Query(default=False),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Empirical model/provider routing leaderboard and task-complexity suitability matrix."""
    return await compute_report_cards_payload(
        since=since,
        role=role,
        provider=provider,
        include_pr_facts=include_pr_facts,
    )


@router.get("/bean-counter", response_model=StaffBeanCounterResponse)
async def get_staff_bean_counter(
    since: str | None = Query(default=None, max_length=40),
    role: str | None = Query(default=None, max_length=64),
    provider: str | None = Query(default=None, max_length=32),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Zero-LLM-cost Bean-Counter token economy & waste ledger."""
    payload = await compute_report_cards_payload(
        since=since,
        role=role,
        provider=provider,
        include_pr_facts=False,
    )
    return {
        "machine": payload["machine"],
        "since": payload["since"],
        "total_runs_analyzed": payload["total_runs_analyzed"],
        "bean_counter_ledger": payload["bean_counter_ledger"],
        "routing_recommendations": payload["routing_recommendations"],
        "leaderboard": payload["leaderboard"],
    }
