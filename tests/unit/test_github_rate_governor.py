"""Unit tests for shared GitHub rate-limit governor (#4278 I-12)."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from github_rate_governor import (
    GitHubGovernorExhausted,
    GitHubRateGovernor,
    acquire_before_github_call,
    get_governor,
    reset_governors_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_governors() -> None:
    reset_governors_for_tests()
    yield
    reset_governors_for_tests()


def test_bucket_refill_allows_acquire_after_wait() -> None:
    gov = GitHubRateGovernor(identity="test", capacity=2.0, refill_per_sec=10.0, max_wait_sec=5.0)
    gov.acquire(cost=2.0)
    with pytest.raises(GitHubGovernorExhausted):
        gov.acquire(cost=1.0, max_wait=0.05)
    time.sleep(0.15)
    gov.acquire(cost=1.0, max_wait=1.0)
    assert gov.tokens_available() >= 0.0


def test_bucket_exhaustion_bounded_wait() -> None:
    gov = GitHubRateGovernor(identity="test", capacity=1.0, refill_per_sec=0.01, max_wait_sec=0.2)
    gov.acquire(cost=1.0)
    started = time.monotonic()
    with pytest.raises(GitHubGovernorExhausted):
        gov.acquire(cost=1.0, max_wait=0.15)
    assert time.monotonic() - started < 0.5


def test_httpx_and_gh_paths_share_one_bucket() -> None:
    identity = "test-install:app:1:2"
    gov_a = get_governor(identity)
    gov_b = get_governor(identity)
    assert gov_a is gov_b
    gov_a.configure_for_tests(capacity=3.0, refill_per_sec=0.0)
    gov_a.acquire(cost=2.0)
    gov_b.acquire(cost=1.0)
    with pytest.raises(GitHubGovernorExhausted):
        gov_a.acquire(cost=1.0, max_wait=0.05)


def test_observe_headers_syncs_remaining() -> None:
    gov = GitHubRateGovernor(identity="test", capacity=5000.0, refill_per_sec=1.0, max_wait_sec=1.0)
    gov.acquire(cost=100.0)
    reset_at = int(time.time()) + 120
    gov.observe_response_headers(
        {
            "X-RateLimit-Remaining": "12",
            "X-RateLimit-Reset": str(reset_at),
        }
    )
    assert gov.tokens_available() == pytest.approx(12.0, abs=0.01)


def test_stale_header_response_does_not_replenish_tokens() -> None:
    """Out-of-order responses must not raise the balance above concurrent acquires."""
    reset_at = int(time.time()) + 3600
    headers_window = {
        "X-RateLimit-Remaining": "100",
        "X-RateLimit-Reset": str(reset_at),
    }
    gov = GitHubRateGovernor(identity="test", capacity=100.0, refill_per_sec=0.0, max_wait_sec=1.0)
    gov.observe_response_headers(headers_window)
    assert gov.tokens_available() == pytest.approx(100.0, abs=0.01)

    for _ in range(5):
        gov.acquire(cost=1.0)
    assert gov.tokens_available() == pytest.approx(95.0, abs=0.01)

    # Stale response: issued when remaining was 99, arrives after five local acquires.
    gov.observe_response_headers(
        {
            "X-RateLimit-Remaining": "99",
            "X-RateLimit-Reset": str(reset_at),
        }
    )
    assert gov.tokens_available() == pytest.approx(95.0, abs=0.01)

    # Fresher (lower) remaining in the same window still reconciles downward.
    gov.observe_response_headers(
        {
            "X-RateLimit-Remaining": "90",
            "X-RateLimit-Reset": str(reset_at),
        }
    )
    assert gov.tokens_available() == pytest.approx(90.0, abs=0.01)


def test_new_rate_limit_window_from_headers_restores_budget() -> None:
    gov = GitHubRateGovernor(identity="test", capacity=100.0, refill_per_sec=0.0, max_wait_sec=1.0)
    old_reset = int(time.time()) + 10
    new_reset = old_reset + 3600
    gov.observe_response_headers(
        {"X-RateLimit-Remaining": "5", "X-RateLimit-Reset": str(old_reset)},
    )
    assert gov.tokens_available() == pytest.approx(5.0, abs=0.01)

    gov.observe_response_headers(
        {"X-RateLimit-Remaining": "100", "X-RateLimit-Reset": str(new_reset)},
    )
    assert gov.tokens_available() == pytest.approx(100.0, abs=0.01)

    # Responses tied to the prior window must not inflate the new one.
    gov.observe_response_headers(
        {"X-RateLimit-Remaining": "80", "X-RateLimit-Reset": str(old_reset)},
    )
    assert gov.tokens_available() == pytest.approx(100.0, abs=0.01)


def test_secondary_limit_backoff_blocks_acquire() -> None:
    gov = GitHubRateGovernor(identity="test", capacity=100.0, refill_per_sec=100.0, max_wait_sec=0.1)
    gov.record_rate_limit(retry_after_seconds=30)
    with pytest.raises(GitHubGovernorExhausted):
        gov.acquire(cost=1.0, max_wait=0.05)
    assert gov.backoff_remaining_seconds() >= 25


def test_record_rate_limit_from_403_headers() -> None:
    gov = GitHubRateGovernor(identity="test", capacity=10.0, refill_per_sec=10.0, max_wait_sec=0.1)
    gov.record_rate_limit_from_headers(
        status_code=403,
        headers={"Retry-After": "45"},
        body="You have exceeded a secondary rate limit.",
    )
    assert gov.backoff_remaining_seconds() >= 40


def test_async_acquire_parallel_safe() -> None:
    gov = GitHubRateGovernor(identity="test", capacity=4.0, refill_per_sec=0.0, max_wait_sec=2.0)
    errors: list[Exception] = []

    async def worker() -> None:
        try:
            await gov.acquire_async(cost=1.0, max_wait=1.0)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    async def run() -> None:
        await asyncio.gather(*(worker() for _ in range(4)))
        with pytest.raises(GitHubGovernorExhausted):
            await gov.acquire_async(cost=1.0, max_wait=0.05)

    asyncio.run(run())
    assert not errors


def test_subprocess_then_httpx_paths_share_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both backend call paths draw from one governor per installation."""
    import github_rate_governor as mod

    identity = "shared-path-test"
    gov = get_governor(identity)
    gov.configure_for_tests(capacity=1.0, refill_per_sec=0.0)
    monkeypatch.setattr(mod, "_default_identity", lambda: identity)

    acquire_before_github_call()
    with pytest.raises(GitHubGovernorExhausted):
        gov.acquire(cost=1.0, max_wait=0.05)


def test_threaded_acquire_serializes_on_lock() -> None:
    gov = GitHubRateGovernor(identity="test", capacity=2.0, refill_per_sec=0.0, max_wait_sec=2.0)
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait(timeout=2)
        gov.acquire(cost=1.0, max_wait=1.0)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)
    with pytest.raises(GitHubGovernorExhausted):
        gov.acquire(cost=1.0, max_wait=0.05)
