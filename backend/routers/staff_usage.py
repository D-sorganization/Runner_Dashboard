"""Staff Hub usage ledger API (epic #1192, issue #1200).

Routes (under ``/api/staff``; the ``/api/staff/`` alt-auth prefix applies):
  GET  /usage           Cost/token/wall-time totals grouped by provider, role or day,
                        plus the daily budget from ``STAFF_BUDGET_USD_PER_DAY``.
  GET  /usage/pricing   The price table this node uses to estimate cost.
  POST /usage/export    Append today's per-provider totals to Repository_Management
                        ``data/credit_usage.json`` (subprocess to the RM script).

Kept out of ``routers/staff.py`` so it lands alongside #1195/#1196 without conflicts.
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, require_scope
from staff import usage
from staff.pricing import price_table_rows
from staff.runner import get_runner
from staff.store import USAGE_GROUPS

log = logging.getLogger("dashboard.staff.usage")
router = APIRouter(prefix="/api/staff", tags=["staff"])


def _caller_name(principal: Principal) -> str:
    if principal.id in (
        "fleet-peer",
        "__loopback__",
        "loopback-dev",
        "test-orchestrator",
        "test-peer",
    ):
        return principal.id
    return f"principal:{principal.id}"


@router.get("/usage")
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


@router.get("/usage/pricing")
async def get_pricing(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    return {"rows": price_table_rows()}


@router.post("/usage/export")
async def export_usage(
    caller: Principal = Depends(require_scope("staff.admin")),
) -> dict[str, Any]:
    """Append today's per-provider totals to RM ``data/credit_usage.json``.

    503 when no Repository_Management checkout (``STAFF_RM_ROOT`` or sibling)
    with ``scripts/append_credit_usage.py`` is available on this node.
    """
    try:
        result = usage.export_to_rm(get_runner().store)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    caller_str = _caller_name(caller)
    log.info(
        "staff: usage export by %s → %s",
        caller_str,
        "ok" if result["ok"] else "partial failure",
    )
    return result
