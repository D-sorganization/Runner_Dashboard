"""Fleet event-log feed endpoint (issue #863).

Serves the durable, ring-buffered history of fleet events recorded by the
fleet-status poller (see ``backend/fleet_events.py``) so the frontend can render
a scrollable event log + alarm panel instead of screen-covering pop-ups.

Read-only and side-effect free: the store is populated by the poller, this
router only reads the most-recent slice. Newest-first ordering matches the
append-only log presentation.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fleet_events import get_event_store

log = logging.getLogger("dashboard.events")
router = APIRouter(tags=["events"])

# Hard cap on a single response so a misbehaving client cannot ask for an
# unbounded slice. The store itself is bounded too (DEFAULT_CAPACITY).
MAX_LIMIT = 500


@router.get("/api/events")
async def get_events(
    limit: int = Query(
        default=200,
        ge=1,
        le=MAX_LIMIT,
        description="Maximum number of most-recent events to return.",
    ),
) -> dict[str, object]:
    """Return the most-recent fleet events, newest first.

    Response shape:
      ``{"events": [{ts, severity, kind, title, detail, node?}, ...],
         "count": <int>, "capacity": <int>}``
    """
    store = get_event_store()
    events = store.recent(limit=limit)
    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
        "capacity": store.capacity,
    }
