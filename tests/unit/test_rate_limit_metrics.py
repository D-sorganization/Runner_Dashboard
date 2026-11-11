"""Unit tests for the count-only 429/throttled-seconds tally (#4250).

``rate_limit.py`` raises ``HTTPException(429, ...)`` on the fly with no persisted
record. This is a small, in-process, count-only addition: no DB, no cross-restart
persistence. It exists so ``GET /api/v1/staff/review/metrics`` can report "dispatch
429 count and total throttled seconds" per the issue's acceptance criteria.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from identity import Principal
from staff.rate_limit import (
    TokenBucketLimiter,
    check_rate_limit,
    rate_limit_metrics_snapshot,
    record_rate_limit_exceeded,
    reset_rate_limit_metrics,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_rate_limit_metrics()


def test_snapshot_starts_empty() -> None:
    assert rate_limit_metrics_snapshot() == {"count_429": 0, "throttled_seconds": 0.0}


def test_record_rate_limit_exceeded_accumulates() -> None:
    record_rate_limit_exceeded(10.0)
    record_rate_limit_exceeded(5.5)
    snap = rate_limit_metrics_snapshot()
    assert snap["count_429"] == 2
    assert snap["throttled_seconds"] == pytest.approx(15.5)


def test_check_rate_limit_429_increments_metrics() -> None:
    limiter = TokenBucketLimiter(capacity={"dispatches": 1}, rate_per_sec={"dispatches": 0.0})
    bot = Principal(id="agent-x", type="bot", name="X", roles=[], scopes=[])
    check_rate_limit("dispatches", bot, limiter=limiter)  # consumes the one token
    with pytest.raises(HTTPException) as exc_info:
        check_rate_limit("dispatches", bot, limiter=limiter)
    assert exc_info.value.status_code == 429
    snap = rate_limit_metrics_snapshot()
    assert snap["count_429"] == 1
    assert snap["throttled_seconds"] > 0
