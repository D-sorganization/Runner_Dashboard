"""compute_backoff / compute_next_attempt_at contract tests for malformed input."""

from __future__ import annotations

import importlib
import math
from datetime import datetime

import pytest
from staff.retry_policy import compute_backoff, compute_next_attempt_at

from staff import retry_policy


def test_huge_attempt_saturates_at_cap_instead_of_overflowing() -> None:
    # 2.0 ** 1999 overflows a float; the backoff must simply saturate at the cap.
    assert compute_backoff(2000, base_seconds=2.0, max_seconds=300.0, jitter=False) == 300.0


def test_negative_cap_never_yields_negative_delay() -> None:
    assert compute_backoff(3, base_seconds=2.0, max_seconds=-5.0, jitter=False) == 0.0


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_base_yields_finite_non_negative_delay(bad: float) -> None:
    result = compute_backoff(2, base_seconds=bad, max_seconds=300.0, jitter=False)
    assert math.isfinite(result)
    assert 0.0 <= result <= 300.0


def test_nan_cap_yields_finite_delay() -> None:
    result = compute_backoff(2, base_seconds=2.0, max_seconds=math.nan, jitter=False)
    assert math.isfinite(result)
    assert result >= 0.0


def test_normal_exponential_values_unchanged() -> None:
    assert compute_backoff(1, 2.0, 300.0, jitter=False) == 2.0
    assert compute_backoff(4, 2.0, 300.0, jitter=False) == 16.0
    assert compute_backoff(20, 2.0, 300.0, jitter=False) == 300.0


def test_next_attempt_at_infinite_delay_does_not_raise() -> None:
    stamp = compute_next_attempt_at(math.inf)
    assert stamp.endswith("Z")
    assert datetime.fromisoformat(stamp.replace("Z", "+00:00")).year >= 2026


def test_next_attempt_at_nan_delay_means_now() -> None:
    before = datetime.now().astimezone()
    stamp = compute_next_attempt_at(math.nan)
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert abs((parsed - before).total_seconds()) < 5


def test_malformed_env_does_not_crash_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_RETRY_BASE_SECONDS", "two")
    monkeypatch.setenv("STAFF_RETRY_MAX_SECONDS", "")
    monkeypatch.setenv("STAFF_FALLBACK_CHAIN_CAP", "x")
    try:
        mod = importlib.reload(retry_policy)
        assert mod.DEFAULT_BASE_BACKOFF_SECONDS == 2.0
        assert mod.DEFAULT_MAX_BACKOFF_SECONDS == 300.0
        assert mod.DEFAULT_FALLBACK_CHAIN_CAP == 3
        # call-time env reads must also fall back, not raise ValueError
        assert mod.compute_backoff(2, jitter=False) == 4.0
    finally:
        monkeypatch.undo()
        importlib.reload(retry_policy)
