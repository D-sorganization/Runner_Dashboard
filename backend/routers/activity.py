"""Read-only API for the versioned fleet activity metadata log."""

from __future__ import annotations

from activity import ActivityPage, get_activity_store
from fastapi import APIRouter, Depends, Query
from identity import Principal, require_scope

router = APIRouter(prefix="/api/v1/activity", tags=["activity"])


@router.get("", response_model=ActivityPage)
def read_activity(
    since: int = Query(default=0, ge=0),
    repo: str | None = Query(default=None, max_length=128),
    node: str | None = Query(default=None, max_length=128),
    session_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100, ge=1, le=500),
    _caller: Principal = Depends(require_scope("activity.read")),  # noqa: B008
) -> ActivityPage:
    """Return a bounded cursor page; readers cannot mutate stored metadata."""
    return get_activity_store().read(since=since, repo=repo, node=node, session_id=session_id, limit=limit)
