"""Strict browser budgets isolate immutable owners/threads and fail closed."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from staff.rate_limit import BrowserRateLimiter

pytestmark = pytest.mark.unit


def test_owner_thread_action_isolation_and_refill() -> None:
    now = [0.0]
    limiter = BrowserRateLimiter(clock=lambda: now[0])
    for _ in range(30):
        assert limiter.acquire("123", "th_one", "messages") == (True, 0.0)
    assert limiter.acquire("123", "th_one", "messages") == (False, 2.0)
    assert limiter.acquire("456", "th_one", "messages")[0]
    assert limiter.acquire("123", "th_two", "messages")[0]
    for _ in range(10):
        assert limiter.acquire("123", "th_one", "dispatches")[0]
    assert limiter.acquire("123", "th_one", "dispatches") == (False, 360.0)
    now[0] = 2.0
    assert limiter.acquire("123", "th_one", "messages")[0]
    assert not limiter.acquire("123", "th_one", "messages")[0]


def test_simultaneous_burst_is_atomic() -> None:
    limiter = BrowserRateLimiter(clock=lambda: 0.0)
    barrier = Barrier(40)

    def acquire(_: int) -> bool:
        barrier.wait(timeout=10)
        return limiter.acquire("123", "th_shared", "messages")[0]

    with ThreadPoolExecutor(max_workers=40) as pool:
        assert sum(pool.map(acquire, range(40))) == 30


def test_capacity_exhaustion_does_not_evict_live_budgets() -> None:
    now = [0.0]
    limiter = BrowserRateLimiter(clock=lambda: now[0], max_buckets=1)
    assert limiter.acquire("123", "th_one", "messages")[0]
    assert not limiter.acquire("123", "th_two", "messages")[0]
    # Only a fully replenished entry may be discarded; no reset by churn.
    now[0] = 2.0
    assert limiter.acquire("123", "th_two", "messages")[0]
    assert not limiter.acquire("123", "th_one", "messages")[0]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_invalid_clock_fails_closed(value: float) -> None:
    limiter = BrowserRateLimiter(clock=lambda: value)
    assert limiter.acquire("123", "th_one", "messages") == (False, 60.0)


def test_unavailable_store_clock_fails_closed() -> None:
    def unavailable() -> float:
        raise OSError("clock unavailable")

    assert BrowserRateLimiter(clock=unavailable).acquire("123", "th_one", "messages") == (False, 60.0)


@pytest.mark.parametrize(
    ("owner", "thread", "action"),
    [
        ("", "th_one", "messages"),
        ("owner", "th_one", "messages"),
        ("123", "", "messages"),
        ("123", "../thread", "messages"),
        ("123", "x" * 129, "messages"),
        ("123", "th_one", "unknown"),
    ],
)
def test_invalid_bucket_identity_fails_closed(owner: str, thread: str, action: str) -> None:
    assert BrowserRateLimiter().acquire(owner, thread, action) == (False, 60.0)


def test_clock_rollback_cannot_mint_tokens() -> None:
    now = [10.0]
    limiter = BrowserRateLimiter(clock=lambda: now[0])
    for _ in range(30):
        assert limiter.acquire("123", "th_one", "messages")[0]
    now[0] = 0.0
    assert not limiter.acquire("123", "th_one", "messages")[0]
    now[0] = 10.0
    assert not limiter.acquire("123", "th_one", "messages")[0]


@pytest.mark.parametrize("capacity", [0, -1, 4097])
def test_invalid_capacity_is_rejected(capacity: int) -> None:
    with pytest.raises(ValueError):
        BrowserRateLimiter(max_buckets=capacity)
