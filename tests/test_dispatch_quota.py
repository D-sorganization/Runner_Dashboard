"""Tests for backend/dispatch_quota.py — issue #386."""

from __future__ import annotations

import asyncio

import dispatch_quota as dq


def _make_quota(max_per_window: int = 10, window_seconds: int = 3600) -> dq.DispatchQuota:
    t = 0.0

    def fixed_clock() -> float:
        return t

    return dq.DispatchQuota(
        window_seconds=window_seconds,
        max_per_window=max_per_window,
        time_fn=fixed_clock,
    )


# ---------------------------------------------------------------------------
# is_anonymous
# ---------------------------------------------------------------------------


def test_is_anonymous_empty_string() -> None:
    q = _make_quota()
    assert q.is_anonymous("") is True


def test_is_anonymous_none() -> None:
    q = _make_quota()
    assert q.is_anonymous(None) is True


def test_is_anonymous_sentinel_strings() -> None:
    q = _make_quota()
    for s in ("anonymous", "none", "null", "unknown", "-"):
        assert q.is_anonymous(s) is True, f"Expected {s!r} to be anonymous"


def test_is_anonymous_real_principal() -> None:
    q = _make_quota()
    assert q.is_anonymous("agent:claude") is False


# ---------------------------------------------------------------------------
# check_and_record — anonymous principal
# ---------------------------------------------------------------------------


def test_check_and_record_anonymous_rejected() -> None:
    q = _make_quota()
    result = asyncio.run(q.check_and_record(None))
    assert result["allowed"] is False
    assert result["reason"] == "anonymous_principal"
    assert result["retry_after"] == 0


# ---------------------------------------------------------------------------
# check_and_record — happy path and rate-limit path
# ---------------------------------------------------------------------------


def test_check_and_record_allowed_within_limit() -> None:
    q = _make_quota(max_per_window=5)
    result = asyncio.run(q.check_and_record("agent:claude"))
    assert result["allowed"] is True
    assert result["reason"] == "ok"


def test_check_and_record_rate_limited_after_cap() -> None:
    q = _make_quota(max_per_window=2)

    async def run() -> dq.QuotaCheck:
        await q.check_and_record("user:alice")
        await q.check_and_record("user:alice")
        return await q.check_and_record("user:alice")

    result = asyncio.run(run())
    assert result["allowed"] is False
    assert result["reason"] == "rate_limited"
    assert result["retry_after"] >= 1


def test_check_and_record_different_principals_independent() -> None:
    q = _make_quota(max_per_window=1)

    async def run() -> tuple[dq.QuotaCheck, dq.QuotaCheck]:
        r1 = await q.check_and_record("user:alice")
        r2 = await q.check_and_record("user:bob")
        return r1, r2

    r1, r2 = asyncio.run(run())
    assert r1["allowed"] is True
    assert r2["allowed"] is True


# ---------------------------------------------------------------------------
# METRICS dict structure
# ---------------------------------------------------------------------------


def test_metrics_has_expected_keys() -> None:
    for key in ("allowed", "rejected_anonymous", "rejected_rate_limited", "current_principals"):
        assert key in dq.METRICS
