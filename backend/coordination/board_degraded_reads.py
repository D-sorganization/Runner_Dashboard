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


def _listed(session: str, data: dict[str, Any]) -> bool:
    """True when ``session`` is a parsed row on ``data``."""
    return any(str(row.get("session")) == session for row in (data.get("sessions") or []))


def _warning_texts(data: dict[str, Any]) -> list[str]:
    """Structured and legacy warning strings, or one placeholder when both are empty."""
    combined = [*(data.get("read_warnings") or []), *(data.get("warnings") or [])]
    texts = [str(item) for item in combined]
    return texts or ["incomplete board read"]


def _fatal_non_limit(text: str) -> bool:
    """True for outage markers that are not themselves a truncation notice."""
    if _limit_warning(text):
        return False
    lowered = text.casefold()
    return any(marker in lowered for marker in _FATAL_DEGRADED_READ_MARKERS)


def _history_class(data: dict[str, Any], texts: list[str]) -> str:
    """``truncated``, ``malformed``, or ``fatal``.

    Pre: ``texts`` is the warning list for an incomplete read. Post: ``truncated`` when an
    unread page may hide a release and no outage marker is present; ``malformed`` when every
    warning is tolerated comment noise and there is no active conflict; otherwise ``fatal``.
    """
    if data.get("active_conflicts") or any(_fatal_non_limit(item) for item in texts):
        return "fatal"
    truncated = bool(data.get("comment_limit_exceeded")) or any(_limit_warning(item) for item in texts)
    if truncated:
        return "truncated"
    if tolerated_malformed_read_warnings(texts):
        return "malformed"
    return "fatal"


def live_session_verdict(session: str, data: dict[str, Any]) -> tuple[bool, str | None]:
    """Return ``(is_live, error)``. A non-empty ``error`` must be raised by the caller.

    Pre: ``data`` is one parsed board read. Post: ``(True, None)`` when that read is complete
    and ``session`` is listed, or when the only defect is tolerated malformed noise and
    ``session`` is listed and not in ``release_uncertain_sessions``. Truncation, and a broken
    release that names ``session``, return ``(False, None)``. Unavailable boards, active
    conflicts, and partial outages return ``(False, detail)``.
    """
    if not data.get("available"):
        return False, f"board unavailable, cannot confirm presence of {session}: {data.get('reason')}"
    if data.get("complete", True):
        return _listed(session, data), None
    texts = _warning_texts(data)
    kind = _history_class(data, texts)
    if kind == "truncated":
        _LOG.warning("board history truncated; a release of %s may have been skipped: %s", session, "; ".join(texts))
        return False, None
    if kind == "malformed":
        uncertain = {str(item) for item in (data.get("release_uncertain_sessions") or [])}
        if _listed(session, data) and session not in uncertain:
            return True, None
        _LOG.warning("board history malformed; %s is not independently present: %s", session, "; ".join(texts))
        return False, None
    return False, f"board read incomplete, cannot confirm presence of {session}: {'; '.join(texts)}"
