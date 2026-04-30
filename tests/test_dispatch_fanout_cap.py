"""Tests for per-principal hourly dispatch cap (issue #408).

Covers:
- Anonymous / empty / synthetic principals are rejected with status_code=422
  before any dispatch is attempted (no gh invocation, no audit append).
- A principal that exceeds ``MAX_PER_PRINCIPAL_PER_HOUR`` in a rolling hour
  receives status_code=429 and a positive ``retry_after`` value.
- The sliding window resets after ``WINDOW_SECONDS`` elapse (verified with a
  monkeypatched ``time_fn``).
- Module-level metrics counters reflect allowed and rejected dispatches.
"""

from __future__ import annotations  # noqa: E402

import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import dispatch_quota  # noqa: E402
from agent_dispatch_router import (  # noqa: E402
    BulkDispatchResponse,
    DispatchSelection,
    PRDispatchRequest,
    dispatch_to_prs,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_run_cmd(returncode: int = 0, stdout: str = "", stderr: str = "") -> AsyncMock:
    async def _run(cmd: list[str], timeout: int = 30, cwd: Path | None = None) -> tuple[int, str, str]:
        return returncode, stdout, stderr

    return AsyncMock(side_effect=_run)


def _normalize(value: str) -> tuple[str, str]:
    if "/" in value:
        parts = value.split("/", 1)
        return parts[1], value
    return value, f"D-sorganization/{value}"


def _avail_patch(available: bool = True):
    detail = "ready" if available else "missing binary"
    status = "available" if available else "missing_binary"
    return patch(
        "agent_dispatch_router.agent_remediation.probe_provider_availability",
        return_value={
            "claude_code_cli": type(
                "A",
                (),
                {"available": available, "status": status, "detail": detail},
            )(),
        },
    )


class _ManualClock:
    """Deterministic clock injected into ``DispatchQuota`` for tests."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


@pytest.fixture(autouse=True)
def _reset_quota():
    """Replace the module singleton with a fresh, deterministic instance."""
    clock = _ManualClock()
    fresh = dispatch_quota.DispatchQuota(time_fn=clock)
    original = dispatch_quota.quota
    original_metrics = dict(dispatch_quota.METRICS)
    dispatch_quota.quota = fresh
    for key in list(dispatch_quota.METRICS):
        dispatch_quota.METRICS[key] = 0
    try:
        yield clock
    finally:
        dispatch_quota.quota = original
        dispatch_quota.METRICS.clear()
        dispatch_quota.METRICS.update(original_metrics)


def _build_request(principal: str = "user-1", number: int = 1) -> PRDispatchRequest:
    return PRDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=number,
        ),
        provider="claude_code_cli",
        prompt="hello world prompt",
        principal=principal,
    )


async def _dispatch(req: PRDispatchRequest, run_cmd_fn: AsyncMock | None = None):
    if run_cmd_fn is None:
        run_cmd_fn = _make_run_cmd(0)
    with _avail_patch(True):
        return await dispatch_to_prs(
            req,
            run_cmd_fn=run_cmd_fn,
            org="D-sorganization",
            repo_root=Path("."),
            normalize_repository_fn=_normalize,
        )


# ─── Anonymous principal ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("principal", ["", "  ", "anonymous", "none", "null", "unknown", "-"])
async def test_anonymous_principal_returns_422(principal: str) -> None:
    """Empty / synthetic principals must be rejected with 422 and no dispatch."""
    req = _build_request(principal=principal)
    run_cmd = _make_run_cmd(0)
    result = await _dispatch(req, run_cmd_fn=run_cmd)
    assert isinstance(result, dict)
    assert result["status_code"] == 422
    assert "anonymous_principal" in result["error"]
    # No dispatch should have been attempted.
    run_cmd.assert_not_called()
    # Metrics: anonymous rejection counter incremented, no allowed dispatches.
    assert dispatch_quota.METRICS["rejected_anonymous"] >= 1
    assert dispatch_quota.METRICS["allowed"] == 0


# ─── Rate-limit cap ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_principal_hits_hourly_cap_returns_429() -> None:
    """The 101st dispatch in a rolling hour for the same principal returns 429."""
    cap = dispatch_quota.MAX_PER_PRINCIPAL_PER_HOUR

    # Burn the full quota with successful dispatches.
    for i in range(cap):
        result = await _dispatch(_build_request(principal="user-cap", number=i + 1))
        assert isinstance(result, BulkDispatchResponse), f"call {i} should succeed"
        assert result.accepted == 1

    # The next call must be rate limited.
    over = await _dispatch(_build_request(principal="user-cap", number=cap + 1))
    assert isinstance(over, dict)
    assert over["status_code"] == 429
    assert "rate_limited" in over["error"]
    assert int(over["retry_after"]) > 0
    assert int(over["retry_after"]) <= dispatch_quota.WINDOW_SECONDS + 1
    assert dispatch_quota.METRICS["rejected_rate_limited"] >= 1


@pytest.mark.asyncio
async def test_cap_is_per_principal(_reset_quota: _ManualClock) -> None:
    """Burning quota for principal A must not block principal B."""
    cap = dispatch_quota.MAX_PER_PRINCIPAL_PER_HOUR
    for i in range(cap):
        await _dispatch(_build_request(principal="user-a", number=i + 1))
    # principal A is now at the cap.
    blocked = await _dispatch(_build_request(principal="user-a", number=cap + 1))
    assert isinstance(blocked, dict) and blocked["status_code"] == 429

    # principal B has its own bucket and should still succeed.
    ok = await _dispatch(_build_request(principal="user-b", number=1))
    assert isinstance(ok, BulkDispatchResponse)
    assert ok.accepted == 1


# ─── Sliding window reset ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cap_resets_after_window(_reset_quota: _ManualClock) -> None:
    """After ``WINDOW_SECONDS`` elapse, the quota for the principal refreshes."""
    cap = dispatch_quota.MAX_PER_PRINCIPAL_PER_HOUR
    for i in range(cap):
        await _dispatch(_build_request(principal="user-window", number=i + 1))

    blocked = await _dispatch(_build_request(principal="user-window", number=cap + 1))
    assert isinstance(blocked, dict) and blocked["status_code"] == 429

    # Advance the manual clock past the rolling window.
    _reset_quota.advance(dispatch_quota.WINDOW_SECONDS + 1)

    refreshed = await _dispatch(_build_request(principal="user-window", number=cap + 2))
    assert isinstance(refreshed, BulkDispatchResponse)
    assert refreshed.accepted == 1


# ─── Module-level helpers ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_anonymous_classifier() -> None:
    """``is_anonymous`` flags falsy / synthetic identifiers."""
    q = dispatch_quota.DispatchQuota()
    assert q.is_anonymous(None)
    assert q.is_anonymous("")
    assert q.is_anonymous("   ")
    assert q.is_anonymous("anonymous")
    assert q.is_anonymous("UNKNOWN")
    assert not q.is_anonymous("user-1")
    assert not q.is_anonymous("svc:claude")


@pytest.mark.asyncio
async def test_metrics_counters_track_outcomes(_reset_quota: _ManualClock) -> None:
    """METRICS dict reflects allowed and rejected counts."""
    # One allowed dispatch.
    await _dispatch(_build_request(principal="user-metrics", number=1))
    assert dispatch_quota.METRICS["allowed"] == 1

    # One anonymous rejection.
    await _dispatch(_build_request(principal="", number=2))
    assert dispatch_quota.METRICS["rejected_anonymous"] >= 1

    # Force a rate-limit rejection by saturating the bucket.
    cap = dispatch_quota.MAX_PER_PRINCIPAL_PER_HOUR
    for i in range(cap - 1):
        await _dispatch(_build_request(principal="user-metrics", number=10 + i))
    blocked = await _dispatch(_build_request(principal="user-metrics", number=999))
    assert isinstance(blocked, dict) and blocked["status_code"] == 429
    assert dispatch_quota.METRICS["rejected_rate_limited"] >= 1
