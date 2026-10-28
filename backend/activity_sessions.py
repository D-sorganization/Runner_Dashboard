"""Restart-stable fleet activity sessions projection (#2860, epic #2856 slice A4).

Every agent/session checked into the fleet, grouped by node and status. A pure
function of persisted :class:`activity.ActivityEnvelope` rows (read only via
the bounded, indexed :meth:`activity.ActivityStore.scan_recent` primitive —
Law of Demeter, never past the store's public API) plus a ``now`` moment, with
no in-memory cache of its own — the acceptance criterion for #2860 is that a
restart reproduces an identical view.

Key rules: session identity is ``(source, session_id)`` (two sources never
merge on a shared id); among a session's events the greatest ``ts`` wins, ties
broken by ``seq``, so a late/out-of-order event can never regress an
already-live session or resurrect an already-ended one; heartbeat absence
alone only ever derives ``stale``, never ``ended`` — ``ended`` requires an
explicit terminal event from ``_TERMINAL_KINDS`` below, and an undocumented
source fails closed to never ``ended``; ``uncaptured`` is reserved for the
not-yet-built T4 slice and is never produced here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from activity import ActivityEnvelope, ActivityStore
from pydantic import BaseModel

# Bounds this to the most recently ingested events only, same full-scan
# avoidance tradeoff activity.read() makes via cursor pagination (#2860).
SESSIONS_SCAN_LIMIT = 4_000

DEFAULT_SESSIONS_LIMIT = 200
MAX_SESSIONS_LIMIT = 500

# live: 1.5x the 60s default heartbeat_interval (backend/staff/watchdog.py) —
# tolerate one missed beat before downgrading to idle.
LIVE_WINDOW_SECONDS = 90.0
# idle: quiet but not alarming yet; stale beyond this.
IDLE_WINDOW_SECONDS = 900.0

SessionStatus = Literal["live", "idle", "stale", "ended", "uncaptured"]

#: Per-source terminal kinds, from the #2859 producer adapters' vocabulary:
#: staff_run (runner.py exit/cancel, dispatch_service.py failed), coordination
#: (service.py presence_release), checkin (routers/activity.py CHECKIN_KINDS'
#: own terminal "done").
_TERMINAL_KINDS: dict[str, frozenset[str]] = {
    "staff_run": frozenset({"exit", "cancel", "failed"}),
    "coordination": frozenset({"presence_release"}),
    "checkin": frozenset({"done"}),
}


class SessionSummary(BaseModel):
    """The latest known state of one ``(source, session_id)`` activity session."""

    model_config = {"frozen": True}

    source: str
    session_id: str
    node: str
    status: SessionStatus
    repo: str | None
    branch: str | None
    issue: int | None
    pr: int | None
    last_kind: str
    last_seq: int
    last_event_id: str
    last_event_at: datetime


class SessionsPage(BaseModel):
    """A bounded page of :class:`SessionSummary` rows."""

    items: list[SessionSummary]
    scan_limit: int
    truncated: bool


def _is_terminal(source: str, kind: str) -> bool:
    return kind in _TERMINAL_KINDS.get(source, frozenset())


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _derive_status(event: ActivityEnvelope, *, now: datetime) -> SessionStatus:
    if _is_terminal(event.source, event.kind):
        return "ended"
    age = (now - _as_utc(event.ts)).total_seconds()
    age = max(age, 0.0)  # a clock-skewed future timestamp reads as "now", not an error
    if age <= LIVE_WINDOW_SECONDS:
        return "live"
    if age <= IDLE_WINDOW_SECONDS:
        return "idle"
    return "stale"


def _group_latest(events: list[ActivityEnvelope]) -> dict[tuple[str, str], ActivityEnvelope]:
    """Reduce to the latest-by-``(ts, seq)`` envelope per ``(source, session_id)``."""
    latest: dict[tuple[str, str], ActivityEnvelope] = {}
    for event in events:
        key = (event.source, event.session_id)
        current = latest.get(key)
        if current is None or (event.ts, event.seq) > (current.ts, current.seq):
            latest[key] = event
    return latest


def _summarize(event: ActivityEnvelope, *, now: datetime) -> SessionSummary:
    return SessionSummary(
        source=event.source,
        session_id=event.session_id,
        node=event.node,
        status=_derive_status(event, now=now),
        repo=event.repo,
        branch=event.branch,
        issue=event.issue,
        pr=event.pr,
        last_kind=event.kind,
        last_seq=event.seq,
        last_event_id=event.event_id,
        last_event_at=event.ts,
    )


def compute_sessions(
    store: ActivityStore,
    *,
    node: str | None = None,
    status: SessionStatus | None = None,
    repo: str | None = None,
    limit: int = DEFAULT_SESSIONS_LIMIT,
    scan_limit: int = SESSIONS_SCAN_LIMIT,
    now: datetime | None = None,
) -> SessionsPage:
    """Restart-stable sessions projection: a pure function of persisted events plus ``now``.

    Precondition: ``1 <= limit <= MAX_SESSIONS_LIMIT`` and
    ``1 <= scan_limit <= activity.MAX_SCAN_LIMIT``; malformed bounds fail closed
    with ``ValueError``. Postcondition: ``len(result.items) <= limit``, and
    status is derived solely from each session's latest persisted event, never
    a cache, so a fresh ``ActivityStore`` handle reproduces identical output.
    """
    if not 1 <= limit <= MAX_SESSIONS_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_SESSIONS_LIMIT}")
    moment = _as_utc(now) if now is not None else datetime.now(UTC)
    raw = store.scan_recent(node=node, repo=repo, limit=scan_limit)
    grouped = _group_latest(raw)
    summaries = [_summarize(event, now=moment) for event in grouped.values()]
    if status is not None:
        summaries = [item for item in summaries if item.status == status]
    summaries.sort(key=lambda item: item.last_event_at, reverse=True)
    return SessionsPage(items=summaries[:limit], scan_limit=scan_limit, truncated=len(raw) >= scan_limit)
