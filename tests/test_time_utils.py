"""Tests for the shared time utilities (issue #404 DRY consolidation)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
import time_utils
from time_utils import now_ms, parse_github_ts, utc_now, utc_now_iso

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


def test_parse_github_ts_z_suffix_is_utc() -> None:
    """A GitHub ``Z`` timestamp parses to an aware UTC datetime."""
    parsed = parse_github_ts("2026-11-16T19:03:13Z")
    assert parsed == datetime(2026, 11, 16, 19, 3, 13, tzinfo=UTC)
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(0)


def test_parse_github_ts_keeps_explicit_offset() -> None:
    """An explicit offset is preserved and compares equal to the UTC instant."""
    parsed = parse_github_ts("2026-11-16T11:03:13-08:00")
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=-8)
    assert parsed == datetime(2026, 11, 16, 19, 3, 13, tzinfo=UTC)


@pytest.mark.parametrize("value", [None, ""])
def test_parse_github_ts_absent_returns_none(value: str | None) -> None:
    """``None`` and the empty string mean "no timestamp", not an error."""
    assert parse_github_ts(value) is None


@pytest.mark.parametrize("value", ["not-a-date", "2026-13-01T00:00:00Z", "2026-11-16T25:00:00Z"])
def test_parse_github_ts_invalid_raises_value_error(value: str) -> None:
    """Malformed input fails loudly with ``ValueError`` rather than returning ``None``."""
    with pytest.raises(ValueError):
        parse_github_ts(value)


def test_now_ms_matches_fixed_utc_instant(monkeypatch: pytest.MonkeyPatch) -> None:
    """``now_ms()`` is the epoch-millisecond value of ``utc_now()``."""
    fixed = datetime(2026, 11, 16, 19, 3, 13, 456789, tzinfo=UTC)
    monkeypatch.setattr(time_utils, "utc_now", lambda: fixed)
    assert now_ms() == 1_794_855_793_456


def test_now_ms_epoch_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Unix epoch maps to zero milliseconds."""
    monkeypatch.setattr(time_utils, "utc_now", lambda: datetime(1970, 1, 1, tzinfo=UTC))
    assert now_ms() == 0
