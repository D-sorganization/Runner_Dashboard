"""GET /api/directives/drain — enforced operator drain-mode gate (#3915 slice 1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pr_inventory
from coordination.drain_directive_gate import (
    DEFAULT_DRAIN_OPEN_PR_THRESHOLD,
    evaluate_repo_drain_gate,
    normalize_repo_query,
    pick_drain_directive,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from identity import require_fleet_peer
from priorities.directives import get_directives

router = APIRouter(prefix="/api/directives", tags=["directives"])


async def fetch_open_non_draft_pr_count(full_repo: str) -> tuple[int, str | None]:
    items, err = await pr_inventory.fetch_repo_prs(full_repo)
    if err:
        return 0, err
    return sum(1 for item in items if isinstance(item, dict) and not item.get("draft")), None


@router.get("/drain")
async def get_directive_drain_status(
    repo: str = Query(..., min_length=1, max_length=200),
    threshold: int = Query(default=DEFAULT_DRAIN_OPEN_PR_THRESHOLD, ge=1, le=10_000),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    try:
        bare_repo, full_repo = normalize_repo_query(repo)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    now = datetime.now(UTC)
    directive = pick_drain_directive(get_directives().active(now), bare_repo, now=now)
    open_pr_count, count_err = await fetch_open_non_draft_pr_count(full_repo)
    payload = evaluate_repo_drain_gate(
        open_pr_count=open_pr_count,
        directive=directive,
        threshold=threshold,
        now=now,
    )
    if count_err:
        payload["warning"] = count_err
    return payload
