"""Kanban board read-only API (epic #4317)."""

# ruff: noqa: B008
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from identity import Principal, require_scope
from kanban_waiting import build_waiting_on_dieter_column
from routers.repo_query import bare_repo_query as _repo

log = logging.getLogger("dashboard.kanban")

router = APIRouter(prefix="/api/v1/kanban", tags=["kanban"])


@router.get("/columns/waiting-on-dieter")
async def get_waiting_on_dieter_column(
    repo: str | None = Query(default=None, max_length=200),
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Read-only Waiting-on-Dieter column from holds, owner labels, and pending approvals."""
    return await run_in_threadpool(build_waiting_on_dieter_column, repo=_repo(repo))
