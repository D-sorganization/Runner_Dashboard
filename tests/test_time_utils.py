"""Tests for the shared time utilities (issue #404 DRY consolidation)."""

from __future__ import annotations

from datetime import UTC, datetime, timezone

from time_utils import utc_now, utc_now_iso

UTC = UTC


def test_utc_now_returns_datetime() -> None:
    """``utc_now()`` returns a ``datetime`` instance."""
    assert isinstance(utc_now(), datetime)


def test_utc_now_is_timezone_aware() -> None:
    """``utc_now()`` always returns a timezone-aware datetime."""
    now = utc_now()
    assert now.tzinfo is not None


def test_utc_now_uses_utc_timezone() -> None:
    """``utc_now()`` returns a datetime anchored to UTC."""
    now = utc_now()
    assert now.utcoffset() == timezone.utc.utcoffset(now)  # noqa: UP017


def test_utc_now_is_recent() -> None:
    """``utc_now()`` returns the present moment, not a stale value."""
    before = datetime.now(timezone.utc)  # noqa: UP017
    sample = utc_now()
    after = datetime.now(timezone.utc)  # noqa: UP017
    assert before <= sample <= after


def test_utc_now_iso_returns_str() -> None:
    """``utc_now_iso()`` returns an ISO-8601 string."""
    iso = utc_now_iso()
    assert isinstance(iso, str)


def test_utc_now_iso_ends_with_z() -> None:
    """The ISO string ends in ``Z`` rather than ``+00:00`` for compactness."""
    iso = utc_now_iso()
    assert iso.endswith("Z")
    assert "+00:00" not in iso


def test_utc_now_iso_round_trip() -> None:
    """``utc_now_iso()`` is parseable back into a UTC datetime."""
    iso = utc_now_iso()
    parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)  # noqa: UP017
