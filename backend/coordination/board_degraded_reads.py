"""Classify board read_warnings for fail-closed presence checks (#4569)."""

from __future__ import annotations

from typing import Any

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
