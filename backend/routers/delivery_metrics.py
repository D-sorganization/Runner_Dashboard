"""Delivery metrics read API (#2121), gated on ``fleet.read`` and feature flag."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from delivery_metrics.config import delivery_metrics_enabled
from delivery_metrics.db import init_db
from delivery_metrics.report import build_metrics_report, pr_drill_down
from delivery_metrics.weekly_summary import build_weekly_summary
from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, require_scope
from session_telemetry import get_session_telemetry_store
from staff.conversations import get_conversation_store
from staff.store import get_run_store

log = logging.getLogger("dashboard.delivery_metrics.api")

router = APIRouter(prefix="/api/v1/metrics", tags=["delivery-metrics"])

_DEFAULT_WINDOW = timedelta(days=14)
_db_path_override: Path | None = None


def set_db_path(path: Path | None) -> None:
    """Test hook: use an isolated SQLite file."""
    global _db_path_override
    _db_path_override = path


def _ensure_enabled() -> None:
    if not delivery_metrics_enabled():
        raise HTTPException(status_code=404, detail="delivery metrics are disabled")


def _parse_bound(value: str | None, *, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name} must be ISO-8601") from exc


def _window(since: str | None, until: str | None) -> tuple[datetime, datetime]:
    end = _parse_bound(until, name="until") or datetime.now(UTC)
    start = _parse_bound(since, name="since") or (end - _DEFAULT_WINDOW)
    if start > end:
        raise HTTPException(status_code=422, detail="since must be before until")
    return start, end


def _with_conn() -> sqlite3.Connection:
    path = init_db(_db_path_override)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _run_report(**kwargs: Any) -> dict[str, Any]:
    conn = _with_conn()
    try:
        return build_metrics_report(conn, **kwargs)
    finally:
        conn.close()


def _load_staff_context(since: datetime, until: datetime) -> tuple[list, list]:
    since_iso = since.isoformat().replace("+00:00", "Z")
    runs = get_run_store().list_runs(limit=500, since=since_iso)
    try:
        proposals = get_conversation_store().list_proposals(limit=500)
    except Exception as exc:
        log.warning("weekly-summary: proposals unavailable: %s", exc)
        proposals = []
    return runs, proposals


def _run_weekly_summary(since: datetime, until: datetime) -> dict[str, Any]:
    conn = _with_conn()
    try:
        runs, proposals = _load_staff_context(since, until)
        window_days = max(1, int((until - since).total_seconds() // 86400))
        session_metrics = get_session_telemetry_store().get_metrics_window(days=window_days)
        return build_weekly_summary(
            conn,
            since=since,
            until=until,
            runs=runs,
            proposals=proposals,
            session_metrics=session_metrics,
        )
    finally:
        conn.close()


def _run_drill_down(repo_full: str, number: int) -> dict[str, Any]:
    conn = _with_conn()
    try:
        body = pr_drill_down(conn, repo_full, number)
    finally:
        conn.close()
    if not body:
        raise HTTPException(status_code=404, detail="pull not found")
    return body


@router.get("/summary")
async def metrics_summary(
    since: str | None = Query(default=None, max_length=40),
    until: str | None = Query(default=None, max_length=40),
    repo_full: str | None = Query(default=None, max_length=200),
    _principal: Principal = Depends(require_scope("fleet.read")),  # noqa: B008
) -> dict[str, Any]:
    """Window summary with PR drill-down (trends, not rankings)."""
    _ensure_enabled()
    start, end = _window(since, until)
    return await asyncio.to_thread(
        _run_report,
        since=start,
        until=end,
        repo_full=repo_full,
    )


@router.get("/trends")
async def metrics_trends(
    since: str | None = Query(default=None, max_length=40),
    until: str | None = Query(default=None, max_length=40),
    repo_full: str | None = Query(default=None, max_length=200),
    _principal: Principal = Depends(require_scope("fleet.read")),  # noqa: B008
) -> dict[str, Any]:
    """Weekly trend buckets only."""
    _ensure_enabled()
    start, end = _window(since, until)
    report = await asyncio.to_thread(
        _run_report,
        since=start,
        until=end,
        repo_full=repo_full,
    )
    return {
        "since": report["since"],
        "until": report["until"],
        "repo_full": report["repo_full"],
        "weekly_trends": report["weekly_trends"],
    }


@router.get("/weekly-summary")
async def metrics_weekly_summary(
    since: str | None = Query(default=None, max_length=40),
    until: str | None = Query(default=None, max_length=40),
    _principal: Principal = Depends(require_scope("fleet.read")),  # noqa: B008
) -> dict[str, Any]:
    """Markdown-friendly weekly JSON for staff consumers (#2123)."""
    _ensure_enabled()
    start, end = _window(since, until)
    return await asyncio.to_thread(_run_weekly_summary, start, end)


@router.get("/pull")
async def metrics_pull(
    repo_full: str = Query(..., min_length=3, max_length=200),
    number: int = Query(..., ge=1),
    _principal: Principal = Depends(require_scope("fleet.read")),  # noqa: B008
) -> dict[str, Any]:
    """Drill-down for one PR and its stored check runs."""
    _ensure_enabled()
    return await asyncio.to_thread(_run_drill_down, repo_full, number)
