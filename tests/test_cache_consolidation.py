"""Tests for cache_utils.Cache (issue #363).

Acceptance criteria verified:
- AC-1: Cache supports per-key TTL, max size, deepcopy-on-set, and
        async get_or_set with stampede protection.
- AC-3: 100 concurrent calls that miss the cache fire factory exactly once.
- AC-4: Cache size never exceeds MAX_CACHE_SIZE under sustained load.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Make backend importable from tests/
_BACKEND = Path(__file__).parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# We need to be careful: cache_utils imports dashboard_config which should be
# available on PYTHONPATH=backend in CI.
from cache_utils import Cache  # noqa: E402

# ---------------------------------------------------------------------------
# Basic get / set / TTL
# ---------------------------------------------------------------------------


class TestCacheBasics:
    def test_set_and_get_within_ttl(self) -> None:
        c = Cache("test", max_size=10)
        c.set("k", "v")
        assert c.get("k", ttl=60) == "v"

    def test_get_expired(self) -> None:
        c = Cache("test", max_size=10)
        c.set("k", "v")
        # TTL of 0 always misses
        assert c.get("k", ttl=0.0) is None

    def test_delete(self) -> None:
        c = Cache("test", max_size=10)
        c.set("x", 42)
        c.delete("x")
        assert c.get("x", ttl=60) is None

    def test_clear(self) -> None:
        c = Cache("test", max_size=10)
        c.set("a", 1)
        c.set("b", 2)
        c.clear()
        assert c.size() == 0

    def test_delete_nonexistent_noop(self) -> None:
        c = Cache("test", max_size=10)
        c.delete("no_such_key")  # Must not raise

    def test_get_nonexistent_returns_none(self) -> None:
        c = Cache("test", max_size=10)
        assert c.get("missing", ttl=60) is None


# ---------------------------------------------------------------------------
# Deep copy on set
# ---------------------------------------------------------------------------


class TestDeepcopyOnSet:
    def test_mutating_original_does_not_corrupt_cache(self) -> None:
        c = Cache("test", max_size=10, deepcopy_on_set=True)
        original = [1, 2, 3]
        c.set("lst", original)
        original.append(99)  # mutate original
        assert c.get("lst", ttl=60) == [1, 2, 3]  # cache still intact

    def test_deepcopy_off_shares_reference(self) -> None:
        c = Cache("test", max_size=10, deepcopy_on_set=False)
        original = [1, 2, 3]
        c.set("lst", original)
        original.append(99)
        # Without deep copy, mutation propagates
        result = c.get("lst", ttl=60)
        assert result == [1, 2, 3, 99]


# ---------------------------------------------------------------------------
# LRU eviction / max size (AC-4)
# ---------------------------------------------------------------------------


class TestMaxSizeEviction:
    def test_size_never_exceeds_max(self) -> None:
        """AC-4: Cache size never exceeds MAX_CACHE_SIZE under sustained load."""
        max_size = 5
        c = Cache("test", max_size=max_size, evict_batch=2)
        for i in range(30):
            c.set(f"key:{i}", i)
        assert c.size() <= max_size, f"Expected ≤{max_size} entries, got {c.size()}"

    def test_eviction_removes_oldest(self) -> None:
        c = Cache("test", max_size=3, evict_batch=1)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)
        c.set("d", 4)  # should evict "a" (oldest)
        assert c.get("a", ttl=60) is None  # evicted
        assert c.get("d", ttl=60) == 4  # newest present

    def test_update_existing_key_does_not_grow_cache(self) -> None:
        c = Cache("test", max_size=5)
        c.set("k", "v1")
        c.set("k", "v2")  # update
        assert c.size() == 1
        assert c.get("k", ttl=60) == "v2"


# ---------------------------------------------------------------------------
# Stampede protection: AC-3
# ---------------------------------------------------------------------------


class TestStampedeProtection:
    @pytest.mark.asyncio
    async def test_concurrent_misses_call_factory_exactly_once(self) -> None:
        """AC-3: 100 concurrent calls that miss the cache fire factory exactly once."""
        c = Cache("test", max_size=100)
        call_count = 0

        async def factory() -> str:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)  # simulate async I/O
            return "expensive_result"

        tasks = [c.get_or_set("key", ttl=60.0, factory=factory) for _ in range(100)]
        results = await asyncio.gather(*tasks)

        assert call_count == 1, f"factory called {call_count} times (expected 1)"
        assert all(r == "expensive_result" for r in results)

    @pytest.mark.asyncio
    async def test_second_request_after_population_hits_cache(self) -> None:
        c = Cache("test", max_size=10)
        call_count = 0

        async def factory() -> int:
            nonlocal call_count
            call_count += 1
            return 42

        await c.get_or_set("k", ttl=60.0, factory=factory)
        await c.get_or_set("k", ttl=60.0, factory=factory)

        assert call_count == 1  # second call hit cache

    @pytest.mark.asyncio
    async def test_different_keys_call_factory_independently(self) -> None:
        c = Cache("test", max_size=100)
        call_counts: dict[str, int] = {}

        async def make_factory(key: str):  # type: ignore[return]
            async def factory() -> str:
                call_counts[key] = call_counts.get(key, 0) + 1
                await asyncio.sleep(0.005)
                return f"result_{key}"

            return factory

        tasks = []
        for k in ["a", "b", "c"]:
            factory = await make_factory(k)
            tasks.extend([c.get_or_set(k, ttl=60.0, factory=factory) for _ in range(10)])

        await asyncio.gather(*tasks)

        for k in ["a", "b", "c"]:
            assert call_counts.get(k) == 1, f"factory for {k!r} called {call_counts.get(k)} times"

    @pytest.mark.asyncio
    async def test_expired_entry_refreshes_via_factory(self) -> None:
        c = Cache("test", max_size=10)
        call_count = 0

        async def factory() -> int:
            nonlocal call_count
            call_count += 1
            return call_count

        # First call — populates cache
        r1 = await c.get_or_set("k", ttl=0.001, factory=factory)
        # Wait for expiry
        await asyncio.sleep(0.01)
        # Second call — should re-invoke factory
        r2 = await c.get_or_set("k", ttl=0.001, factory=factory)

        assert call_count == 2, f"Expected 2 factory calls, got {call_count}"
        assert r1 == 1
        assert r2 == 2


# ---------------------------------------------------------------------------
# cache_size() and named metrics
# ---------------------------------------------------------------------------


class TestCacheSizeMetrics:
    def test_cache_size_returns_named_count(self) -> None:
        c = Cache("my_cache", max_size=20)
        c.set("x", 1)
        c.set("y", 2)
        assert c.size() == 2

    def test_module_level_cache_size_reports_main(self) -> None:
        from cache_utils import cache_size

        # Just verify it returns a dict with "main" key and int value
        result = cache_size()
        assert isinstance(result, dict)
        assert "main" in result
        assert isinstance(result["main"], int)
