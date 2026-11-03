"""Hub drain-mode status for the node-side ``gh`` guard (#3915 slice 1).

  GET /api/directives/drain?repo=<bare repo>
      ``{repo, drained, reason, directive_id, expires, open_pr_count, threshold}``

Reads use ``require_fleet_peer`` like the other directive reads. The open-PR
count is only fetched when the repo has an active drain directive, so undrained
repos cost no GitHub API call.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from drain_mode import active_drain_directive, drain_status, threshold_from_env, validate_repo
from fastapi import APIRouter, Depends, HTTPException, Query
from identity import require_fleet_peer
from priorities.directives import get_directives
from staff.consolidation import open_pr_count

log = logging.getLogger("dashboard.routers.drain")

router = APIRouter(prefix="/api/directives", tags=["directives"])


@router.get("/drain")
async def get_drain_status(
    repo: str = Query(..., max_length=100),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    try:
        validate_repo(repo)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    now = datetime.now(UTC)
    directives = get_directives().active(now)
    count: int | None = None
    if active_drain_directive(repo, directives, now) is not None:
        try:
            count = await asyncio.to_thread(open_pr_count, repo)
        except Exception as exc:  # noqa: BLE001 - an unknown count keeps the drain on
            log.warning("drain status: open PR count for %s unavailable: %s", repo, exc)
    return drain_status(repo, directives, open_pr_count=count, now=now, threshold=threshold_from_env())
