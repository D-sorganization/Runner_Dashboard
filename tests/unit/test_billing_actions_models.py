"""Pure-function tests for the Actions-minutes billing monitor (#2878, slice 1)."""

from __future__ import annotations

from datetime import date

import pytest
from billing_actions.models import (
    SKU_MULTIPLIERS,
    UsageItem,
    compute_quota_usage,
    project_to_reset,
)


def _item(sku: str, minutes: float, *, is_public: bool = False, repo: str = "org/private") -> UsageItem:
    return UsageItem(
        date=date(2026, 10, 1),
        repo_full=repo,
        sku=sku,
        is_public=is_public,
        minutes_used=minutes,
    )


def test_quota_excludes_public_repos_and_applies_sku_multipliers() -> None:
    items = [
        _item("UBUNTU", 10.0),
        _item("WINDOWS", 10.0),
        _item("MACOS", 10.0, repo="org/open-source", is_public=True),
    ]
    result = compute_quota_usage(items)
    assert result.weighted_minutes == pytest.approx(
        10.0 * SKU_MULTIPLIERS["UBUNTU"] + 10.0 * SKU_MULTIPLIERS["WINDOWS"]
    )


def test_unknown_sku_flagged_not_counted_as_1x() -> None:
    items = [_item("UBUNTU", 10.0), _item("QUANTUM", 10.0)]
    result = compute_quota_usage(items)
    assert result.weighted_minutes == pytest.approx(10.0 * SKU_MULTIPLIERS["UBUNTU"])
    assert result.unknown_skus == frozenset({"QUANTUM"})


@pytest.mark.parametrize("method", ["24h", "7d", "cycle"])
def test_project_to_reset_postcondition_total_ge_used(method: str) -> None:
    daily_usage = [
        (date(2026, 10, 1), 100.0),
        (date(2026, 10, 5), 50.0),
        (date(2026, 10, 10), 25.0),
    ]
    result = project_to_reset(
        daily_usage,
        cycle_start=date(2026, 10, 1),
        cycle_end=date(2026, 10, 31),
        now=date(2026, 10, 10),
        included_minutes=1000.0,
        method=method,
    )
    assert result.projected_total_at_reset >= result.used
    assert result.method == method


def test_remaining_minutes_none_iff_included_unset() -> None:
    daily_usage = [(date(2026, 10, 1), 10.0)]
    with_included = project_to_reset(
        daily_usage,
        cycle_start=date(2026, 10, 1),
        cycle_end=date(2026, 10, 31),
        now=date(2026, 10, 2),
        included_minutes=100.0,
        method="cycle",
    )
    without_included = project_to_reset(
        daily_usage,
        cycle_start=date(2026, 10, 1),
        cycle_end=date(2026, 10, 31),
        now=date(2026, 10, 2),
        included_minutes=None,
        method="cycle",
    )
    assert with_included.remaining_minutes is not None
    assert without_included.remaining_minutes is None


def test_project_to_reset_rejects_now_outside_cycle() -> None:
    with pytest.raises(AssertionError):
        project_to_reset(
            [],
            cycle_start=date(2026, 10, 1),
            cycle_end=date(2026, 10, 31),
            now=date(2026, 11, 1),
            included_minutes=None,
            method="cycle",
        )
