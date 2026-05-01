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

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string ending in ``Z``."""
    return utc_now().isoformat().replace("+00:00", "Z")
