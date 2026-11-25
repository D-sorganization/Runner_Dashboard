"""Classify board read_warnings for fail-closed presence checks (#4569)."""

from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger(__name__)

_FATAL_DEGRADED_READ_MARKERS = (
    "board read failed",
    "unclassified",
    "pagination failed",
    "page limit",
    "comment limit",
    "comment-limit",
)
_TOLERATED_MALFORMED_READ_MARKERS = (
    "malformed",
    "invalid coordination envelope",
    "rejected comment without verified",
)


def tolerated_malformed_read_warnings(read_warnings: list[Any]) -> bool:
    """True only when every ``read_warnings`` entry is benign malformed-comment noise (#4569).

    Pre: ``read_warnings`` is the structured list from a board list/inbox parse. Post: returns
    ``False`` when empty, when any entry matches a fatal/partial-outage marker, or when any entry
    is not classified as malformed-comment noise; ``True`` only when all entries are tolerable.
    """
    if not read_warnings:
        return False
    for raw in read_warnings:
        lowered = str(raw).casefold()
        if any(marker in lowered for marker in _FATAL_DEGRADED_READ_MARKERS):
            return False
        if not any(marker in lowered for marker in _TOLERATED_MALFORMED_READ_MARKERS):
            return False
    return True


def _limit_warning(text: str) -> bool:
    """True when ``text`` is a comment-limit or page-limit notice."""
    lowered = text.casefold()
    return "page limit" in lowered or "comment limit" in lowered or "comment-limit" in lowered


def live_session_verdict(session: str, data: dict[str, Any]) -> tuple[bool, str | None]:
    """Return ``(is_live, error)``. A non-empty ``error`` must be raised by the caller.

    Pre: ``data`` is one parsed board read. Post: ``(True, None)`` only when that read
    is complete and ``session`` is in ``sessions``. Incomplete history is not current
    presence. Classified malformed or comment-limit noise returns ``(False, None)``.
    Unavailable boards, active conflicts, and partial outages return ``(False, detail)``.
    """
    if not data.get("available"):
        return False, f"board unavailable, cannot confirm presence of {session}: {data.get('reason')}"
    if data.get("complete", True):
        rows = data.get("sessions") or []
        return any(str(row.get("session")) == session for row in rows), None
    warnings = [str(item) for item in (data.get("warnings") or ["incomplete board read"])]
    combined = [*(data.get("read_warnings") or []), *(data.get("warnings") or [])]
    texts = [str(item) for item in combined]
    truncated = bool(data.get("comment_limit_exceeded")) or any(_limit_warning(item) for item in texts)
    malformed = tolerated_malformed_read_warnings(texts)
    limit_only = all(_limit_warning(item) for item in texts)
    if not data.get("active_conflicts") and (malformed or (truncated and limit_only)):
        _LOG.warning("board history incomplete; %s is not independently present: %s", session, "; ".join(warnings))
        return False, None
    return False, f"board read incomplete, cannot confirm presence of {session}: {'; '.join(warnings)}"
