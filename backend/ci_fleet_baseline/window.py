"""UTC window helpers for per-day API slicing."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator


def parse_utc(iso: str) -> dt.datetime:
    """Parse an ISO-8601 timestamp that ends with Z into aware UTC."""
    if not iso:
        raise ValueError("timestamp must not be empty")
    normalized = iso.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def iter_utc_calendar_days(start_iso: str, end_iso: str) -> Iterator[str]:
    """Yield YYYY-MM-DD for each UTC calendar day from start through end inclusive."""
    start = parse_utc(start_iso).date()
    end = parse_utc(end_iso).date()
    if end < start:
        raise ValueError("end must not be before start")
    day = start
    while day <= end:
        yield day.isoformat()
        day += dt.timedelta(days=1)


def day_created_filter(day: str) -> str:
    """GitHub ``created`` query parameter for one UTC calendar day."""
    if len(day) != 10:
        raise ValueError(f"expected YYYY-MM-DD, got {day!r}")
    return f"{day}T00:00:00Z..{day}T23:59:59Z"


def clamp_measurement_window_end(requested_end_utc: str, collected_at_utc: str) -> str:
    """Return min(requested_end, collected_at) as an ISO-8601 UTC timestamp."""
    effective = min(parse_utc(requested_end_utc), parse_utc(collected_at_utc))
    return effective.strftime("%Y-%m-%dT%H:%M:%SZ")


def strict_window_bounds(
    day: str, window_start: str, window_end: str
) -> tuple[str, str]:
    """Clip one calendar day to the strict measurement window."""
    day_start = parse_utc(f"{day}T00:00:00Z")
    day_end = parse_utc(f"{day}T23:59:59Z")
    win_start = parse_utc(window_start)
    win_end = parse_utc(window_end)
    start = max(day_start, win_start)
    end = min(day_end, win_end)
    if end < start:
        raise ValueError(f"day {day} is outside the measurement window")
    return (
        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
