"""Shared time utilities used across backend modules.

Consolidates the previously duplicated ``_utc_now`` helpers (issue #404 DRY
work) so every module produces UTC timestamps in the same way.

Two helpers are exposed:

- :func:`utc_now` returns a timezone-aware :class:`datetime.datetime`.
- :func:`utc_now_iso` returns an ISO-8601 string ending in ``Z``, matching
  the prior ``_utc_now`` string contract used by dispatch / push / agent
  remediation modules.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(UTC)


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string ending in ``Z``."""
    return utc_now().isoformat().replace("+00:00", "Z")


def now_ms() -> int:
    """Return the current UTC time as integer epoch milliseconds.

    Used by the fleet event log (issue #863) so event timestamps are compact,
    JSON-cheap, and trivially sortable on the frontend.
    """
    return int(utc_now().timestamp() * 1000)
