"""Pure data model and pure functions for the Actions-minutes billing monitor.

Slice 1 of #2878: SKU-weighted quota accounting and reset projection, with no
network or storage dependency so the math can be verified in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

SKU_MULTIPLIERS: dict[str, float] = {
    "UBUNTU": 1.0,
    "WINDOWS": 2.0,
    "MACOS": 10.0,
}

ProjectionMethod = Literal["24h", "7d", "cycle"]

_LOOKBACK_DAYS: dict[ProjectionMethod, int | None] = {
    "24h": 1,
    "7d": 7,
    "cycle": None,
}


@dataclass(frozen=True)
class UsageItem:
    """One SKU/repo/day of Actions usage.

    Pre: ``repo_full`` contains ``/``; ``minutes_used`` is non-negative.
    """

    date: date
    repo_full: str
    sku: str
    is_public: bool
    minutes_used: float

    def __post_init__(self) -> None:
        assert "/" in self.repo_full, "repo_full must be owner/name"
        assert self.minutes_used >= 0.0, "minutes_used must be non-negative"


@dataclass(frozen=True)
class BudgetSnapshot:
    """The org's configured Actions budget cap (slice 2).

    Pre: ``amount_usd`` is non-negative.
    """

    amount_usd: float
    prevent_further_usage: bool

    def __post_init__(self) -> None:
        assert self.amount_usd >= 0.0, "amount_usd must be non-negative"


@dataclass(frozen=True)
class QuotaResult:
    """SKU-weighted minutes used this cycle, private repos only.

    Post: an unknown SKU contributes zero weighted minutes (never counted as
    the 1x default) and is named in ``unknown_skus``.
    """

    weighted_minutes: float
    unknown_skus: frozenset[str]


@dataclass(frozen=True)
class ProjectionResult:
    """Burn-rate projection to the billing cycle reset.

    Post: ``projected_total_at_reset >= used``.
    Post: ``remaining_minutes is None`` iff the caller passed
    ``included_minutes=None`` (no included-minutes plan configured).
    """

    used: float
    projected_total_at_reset: float
    remaining_minutes: float | None
    method: ProjectionMethod


def compute_quota_usage(items: list[UsageItem]) -> QuotaResult:
    """SKU-weighted minutes used, excluding public repos.

    Pre: ``items`` may be empty.
    Post: see :class:`QuotaResult`.
    """
    total = 0.0
    unknown_skus: set[str] = set()
    for item in items:
        if item.is_public:
            continue
        multiplier = SKU_MULTIPLIERS.get(item.sku)
        if multiplier is None:
            unknown_skus.add(item.sku)
            continue
        total += item.minutes_used * multiplier
    return QuotaResult(weighted_minutes=total, unknown_skus=frozenset(unknown_skus))


def project_to_reset(
    daily_usage: list[tuple[date, float]],
    *,
    cycle_start: date,
    cycle_end: date,
    now: date,
    included_minutes: float | None,
    method: ProjectionMethod = "cycle",
) -> ProjectionResult:
    """Project weighted minutes used to the cycle reset.

    ``daily_usage`` is ``(date, weighted_minutes)`` pairs already filtered to
    this cycle; ``method`` picks the trailing window used to estimate the
    daily burn rate (``24h``, ``7d``, or the whole elapsed ``cycle``).

    Pre: ``cycle_start <= now <= cycle_end``.
    Post: ``projected_total_at_reset >= used``.
    Post: ``remaining_minutes is None`` iff ``included_minutes is None``.
    """
    assert cycle_start <= now <= cycle_end, "now must fall within the cycle"
    used = sum(minutes for _, minutes in daily_usage)
    assert used >= 0.0

    elapsed_days = max((now - cycle_start).days, 1)
    window_days = _LOOKBACK_DAYS[method]
    if window_days is None:
        lookback_minutes = used
        lookback_days = elapsed_days
    else:
        lookback_minutes = sum(m for d, m in daily_usage if (now - d).days < window_days)
        lookback_days = min(window_days, elapsed_days)
    daily_rate = lookback_minutes / lookback_days if lookback_days else 0.0

    remaining_days = max((cycle_end - now).days, 0)
    projected_total = max(used + daily_rate * remaining_days, used)

    remaining_minutes = None if included_minutes is None else max(included_minutes - used, 0.0)

    return ProjectionResult(
        used=used,
        projected_total_at_reset=projected_total,
        remaining_minutes=remaining_minutes,
        method=method,
    )
