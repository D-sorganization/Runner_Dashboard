"""Agent outcome scorecard, report cards, leaderboard & bean-counter API (/api/v1/staff).

GET /outcomes?since=<ISO date>&group_by=role|provider|repo
GET /report-cards?since=<ISO date>&role=<role>&provider=<provider>&include_pr_facts=bool
GET /leaderboard?since=<ISO date>&role=<role>&provider=<provider>&include_pr_facts=bool
GET /bean-counter?since=<ISO date>&role=<role>&provider=<provider>
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, require_scope
from staff import outcomes, report_cards
from staff.adapters import available_providers
from staff.models_insights import (
    StaffBeanCounterResponse,
    StaffOutcomesResponse,
    StaffReportCardsResponse,
)
from staff.runner import get_runner

log = logging.getLogger("dashboard.staff.outcomes")
router = APIRouter(tags=["staff"])

RUN_LIMIT = 2000


def _checked_since(since: str | None) -> str:
    """The window start: ``since`` if it parses as an ISO date/time, else 422."""
    if not since:
        return outcomes.default_since()
    try:
        datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="since must be an ISO date or date-time") from exc
    return since


async def compute_report_cards_payload(
    *,
    since: str | None = None,
    role: str | None = None,
    provider: str | None = None,
    include_pr_facts: bool = False,
) -> dict[str, Any]:
    """Shared zero-LLM-cost report cards & leaderboard builder for v1 and legacy routes."""
    start = _checked_since(since)
    runner = get_runner()
    runs = runner.store.list_runs(limit=RUN_LIMIT, since=start)
    facts: dict[Any, Any] = {}
    truncated = False
    if include_pr_facts:
        wanted, truncated = outcomes.prs_to_read(runs)
        facts = await outcomes.read_pr_facts(wanted)
    avail = [k for k, v in available_providers().items() if v] or ["claude", "codex", "antigravity"]
    payload = report_cards.build_report_cards(
        runs,
        facts,
        available_providers=avail,
        role_filter=role or None,
        provider_filter=provider or None,
    )
    return {
        **payload,
        "since": start,
        "machine": runner.machine,
        "prs_truncated": truncated,
    }


@router.get("/outcomes", response_model=StaffOutcomesResponse)
async def get_outcomes(
    since: str | None = Query(default=None, max_length=40),
    group_by: str = Query(default="role", max_length=10),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Scorecard per ``group_by`` for runs created since ``since`` (default: 14 days)."""
    if group_by not in outcomes.GROUP_BY:
        raise HTTPException(status_code=422, detail=f"group_by must be one of {', '.join(outcomes.GROUP_BY)}")
    start = _checked_since(since)
    runner = get_runner()
    runs = runner.store.list_runs(limit=RUN_LIMIT, since=start)
    wanted, truncated = outcomes.prs_to_read(runs)
    facts = await outcomes.read_pr_facts(wanted)
    body = outcomes.aggregate(runs, facts, group_by=group_by)
    return {**body, "since": start, "machine": runner.machine, "prs_truncated": truncated}


@router.get("/report-cards", response_model=StaffReportCardsResponse)
async def get_report_cards(
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
async def get_leaderboard(
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
async def get_bean_counter(
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
