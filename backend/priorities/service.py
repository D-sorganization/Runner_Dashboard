"""Priorities assembly for ``/api/priorities`` and the coordination briefing (issue #1227, epic #1192).

Reads never raise for an upstream problem: a missing RM checkout, meeting or
manifest degrades to ``{"available": false, "reason": ...}`` (HTTP 200) per the
sibling-repos rule, while directives — dashboard-owned state — are always
returned. ``top_priorities`` is the one function other modules import.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from priorities.directives import get_directives
from priorities.sources import SourceUnavailable, latest_consensus, list_meetings, load_portfolios, read_meeting
from staff.workspace import rm_root

NO_RM_REASON = "Repository_Management checkout not found (set STAFF_RM_ROOT)"
NO_MEETING_REASON = "no board meeting with a consensus.md has been held yet"


def _generated_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _directive_dicts() -> list[dict[str, Any]]:
    return [d.to_dict() for d in get_directives().active()]


def _portfolios(root: Path) -> tuple[list[dict[str, Any]], str]:
    try:
        return [p.model_dump() for p in load_portfolios(root)], ""
    except SourceUnavailable as exc:
        return [], str(exc)


def _board(root: Path) -> dict[str, Any] | None:
    found = latest_consensus(root)
    if found is None:
        return None
    meeting_date, consensus = found
    return {"date": meeting_date, **consensus.model_dump()}


def priorities_snapshot() -> dict[str, Any]:
    """``GET /api/priorities``: latest board consensus, active directives and portfolios.

    Postcondition: ``available`` is True exactly when ``board`` is not None.
    """
    root = rm_root()
    snapshot: dict[str, Any] = {"directives": _directive_dicts(), "generated_at": _generated_at()}
    if root is None:
        return {**snapshot, "available": False, "reason": NO_RM_REASON, "board": None, "portfolios": []}
    portfolios, portfolios_reason = _portfolios(root)
    board = _board(root)
    snapshot.update(available=board is not None, board=board, portfolios=portfolios)
    if board is None:
        snapshot["reason"] = NO_MEETING_REASON
    if portfolios_reason:
        snapshot["portfolios_reason"] = portfolios_reason
    assert snapshot["available"] == (snapshot["board"] is not None)
    return snapshot


def meetings_index() -> dict[str, Any]:
    """``GET /api/priorities/meetings``: every dated meeting folder, newest first."""
    root = rm_root()
    if root is None:
        return {"available": False, "reason": NO_RM_REASON, "meetings": [], "generated_at": _generated_at()}
    return {"available": True, "meetings": list_meetings(root), "generated_at": _generated_at()}


def meeting_detail(meeting_date: str) -> dict[str, Any] | None:
    """``GET /api/priorities/meetings/{date}``; None when RM is present but the meeting is not (→ 404).

    Precondition: ``meeting_date`` is a valid ``YYYY-MM-DD`` (the route enforces it).
    """
    root = rm_root()
    if root is None:
        return {"available": False, "reason": NO_RM_REASON, "date": meeting_date, "generated_at": _generated_at()}
    meeting = read_meeting(root, meeting_date)
    if meeting is None:
        return None
    return {"available": True, **meeting, "generated_at": _generated_at()}


def top_priorities(limit: int) -> list[dict[str, Any]]:
    """Active board priorities (by rank) then active directives (by priority), at most ``limit`` items.

    Each item carries ``kind`` = ``"board"`` or ``"directive"``. Never raises for a missing RM
    checkout or meeting (directives alone are returned). Precondition: ``limit >= 0``.
    """
    if limit < 0:
        raise ValueError("limit must be >= 0")
    items: list[dict[str, Any]] = []
    root = rm_root()
    board = _board(root) if root is not None else None
    if board is not None:
        items += [{"kind": "board", "meeting": board["date"], **entry} for entry in board["active"]]
    items += [{"kind": "directive", **d} for d in _directive_dicts()]
    result = items[:limit]
    assert len(result) <= limit
    return result
