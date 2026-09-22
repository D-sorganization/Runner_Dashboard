"""Cron parsing, next-fire computation and run windows for staff roles (issue #1196).

Pure functions, no third-party cron dependency. Five-field cron
(``minute hour day-of-month month day-of-week``) with ``*``, lists, ranges
and steps; month/weekday names are accepted case-insensitively. Day-of-month
and day-of-week combine the classic cron way: when both are restricted a
date matches if *either* does, otherwise the restricted one must match.

Times are wall-clock in the role's zone (default ``America/Los_Angeles``)
and resolved with :mod:`zoneinfo`, so DST is handled by the zone database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Los_Angeles"
_MAX_SEARCH_DAYS = 366 * 5

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


@dataclass(frozen=True)
class CronSpec:
    """Parsed five-field cron expression. Sets are inclusive, weekday 0 = Sunday."""

    expr: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    days_restricted: bool
    weekdays_restricted: bool

    def matches_date(self, when: datetime) -> bool:
        if when.month not in self.months:
            return False
        dom_ok = when.day in self.days
        dow_ok = ((when.weekday() + 1) % 7) in self.weekdays
        if self.days_restricted and self.weekdays_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok


def _atom(token: str, lo: int, hi: int, names: tuple[str, ...] | None) -> int:
    token = token.strip().lower()
    if names and token in names:
        return names.index(token) + (1 if names is _MONTHS else 0)
    if not token.isdigit():
        raise ValueError(f"cron: bad value {token!r}")
    value = int(token)
    if not lo <= value <= hi:
        raise ValueError(f"cron: {value} outside {lo}-{hi}")
    return value


def _parse_field(field: str, lo: int, hi: int, names: tuple[str, ...] | None = None) -> tuple[frozenset[int], bool]:
    """Return (values, restricted). ``restricted`` is False only for a bare ``*``."""
    field = field.strip()
    if not field:
        raise ValueError("cron: empty field")
    values: set[int] = set()
    restricted = field != "*"
    for part in field.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise ValueError(f"cron: bad step {step_s!r}")
            step = int(step_s)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = _atom(a, lo, hi, names), _atom(b, lo, hi, names)
            if start > end:
                raise ValueError(f"cron: range {part!r} is reversed")
        else:
            start = _atom(part, lo, hi, names)
            end = hi if step > 1 else start
        values.update(range(start, end + 1, step))
    return frozenset(values), restricted


def parse_cron(expr: str) -> CronSpec:
    """Parse a five-field cron expression. Raises ``ValueError`` on bad input."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron: expected 5 fields, got {len(fields)} in {expr!r}")
    minutes, _ = _parse_field(fields[0], 0, 59)
    hours, _ = _parse_field(fields[1], 0, 23)
    days, days_r = _parse_field(fields[2], 1, 31)
    months, _ = _parse_field(fields[3], 1, 12, _MONTHS)
    weekdays, wd_r = _parse_field(fields[4], 0, 7, _WEEKDAYS)
    weekdays = frozenset(0 if d == 7 else d for d in weekdays)  # 7 is Sunday too
    return CronSpec(expr.strip(), minutes, hours, days, months, weekdays, days_r, wd_r)


def _localize(when: datetime, tz: str) -> datetime:
    zone = ZoneInfo(tz)
    return when.replace(tzinfo=zone) if when.tzinfo is None else when.astimezone(zone)


def next_fire(expr: str | CronSpec, after: datetime, tz: str = DEFAULT_TZ) -> datetime:
    """First wall-clock minute strictly after ``after`` that matches ``expr``.

    Pre: ``after`` is aware, or naive and interpreted in ``tz``.
    Post: the result is aware in ``tz`` and ``result > after``.
    """
    spec = parse_cron(expr) if isinstance(expr, str) else expr
    zone = ZoneInfo(tz)
    start = _localize(after, tz)
    floor = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = floor.date()
    for _ in range(_MAX_SEARCH_DAYS):
        probe = datetime(day.year, day.month, day.day, tzinfo=zone)
        if spec.matches_date(probe):
            for hour in sorted(spec.hours):
                for minute in sorted(spec.minutes):
                    candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
                    if candidate >= floor:
                        return candidate
        day += timedelta(days=1)
    raise ValueError(f"cron: {spec.expr!r} never fires within {_MAX_SEARCH_DAYS} days")


def parse_hhmm(value: str) -> time:
    """``'22:00'`` → ``time(22, 0)``. Raises ``ValueError`` on bad input."""
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"window: expected HH:MM, got {value!r}")
    return time(int(parts[0]), int(parts[1]))


def in_window(window: dict[str, str] | None, now: datetime, tz: str = DEFAULT_TZ) -> bool:
    """True when ``now`` (in ``tz``) falls inside ``{start, end}``.

    An absent window, or one with an empty ``start``/``end``, is always open.
    ``start > end`` is an overnight window (22:00–06:00): open from ``start``
    until midnight and from midnight until ``end``. ``start == end`` is open
    all day. The start bound is inclusive, the end bound exclusive.
    """
    if not window or not window.get("start") or not window.get("end"):
        return True
    start, end = parse_hhmm(window["start"]), parse_hhmm(window["end"])
    clock = _localize(now, tz).time().replace(second=0, microsecond=0)
    if start == end:
        return True
    if start < end:
        return start <= clock < end
    return clock >= start or clock < end
