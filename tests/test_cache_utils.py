"""Unit tests for backend/cache_utils.py (issue #155)."""

from __future__ import annotations

import sys  # noqa: E402
import time
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import cache_utils  # noqa: E402


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure the global cache is cleared before and after each test."""
    cache_utils.cache_clear()
    yield
    cache_utils.cache_clear()


class TestCacheGet:
    def test_miss_empty_cache(self) -> None:
        assert cache_utils.cache_get("missing", ttl=60) is None

    def test_hit_within_ttl(self) -> None:
        cache_utils.cache_set("key", {"data": 123})
        result = cache_utils.cache_get("key", ttl=60)
        assert result == {"data": 123}

    def test_miss_after_ttl_expires(self) -> None:
        cache_utils.cache_set("key", "value")
        # Simulate passage of time by manipulating the stored timestamp
        original_entry = cache_utils._cache["key"]
        cache_utils._cache["key"] = ("value", original_entry[1] - 100)
        assert cache_utils.cache_get("key", ttl=60) is None

    def test_miss_on_expired_but_key_exists(self) -> None:
        cache_utils.cache_set("key", "stale")
        # Backdate the entry so it's expired
        data, ts = cache_utils._cache["key"]
        cache_utils._cache["key"] = (data, ts - 1000)
        assert cache_utils.cache_get("key", ttl=60) is None

    def test_hit_with_zero_ttl(self) -> None:
        cache_utils.cache_set("key", "value")
        # With ttl=0, any entry is immediately expired
        assert cache_utils.cache_get("key", ttl=0) is None

    def test_ttl_as_float(self) -> None:
        cache_utils.cache_set("key", "value")
        result = cache_utils.cache_get("key", ttl=0.5)
        assert result == "value"


class TestCacheSet:
    def test_stores_value(self) -> None:
        cache_utils.cache_set("key", "value")
        assert cache_utils._cache["key"][0] == "value"

    def test_stores_current_timestamp(self) -> None:
        before = time.time()
        cache_utils.cache_set("key", "value")
        after = time.time()
        stored_ts = cache_utils._cache["key"][1]
        assert before <= stored_ts <= after

    def test_move_to_end_on_update(self) -> None:
        cache_utils.cache_set("key1", "a")
        cache_utils.cache_set("key2", "b")
        # Updating key1 should move it to the end
        original_keys = list(cache_utils._cache.keys())
        assert original_keys == ["key1", "key2"]
        cache_utils.cache_set("key1", "aa")
        new_keys = list(cache_utils._cache.keys())
        assert new_keys == ["key2", "key1"]

    def test_eviction_when_full(self) -> None:
        from unittest.mock import patch

        with patch.object(cache_utils, "MAX_CACHE_SIZE", 3):
            with patch.object(cache_utils, "CACHE_EVICT_BATCH", 1):
                # Populate to capacity
                cache_utils.cache_set("k1", "v1")
                cache_utils.cache_set("k2", "v2")
                cache_utils.cache_set("k3", "v3")
                # Adding a 4th should evict the oldest (k1)
                cache_utils.cache_set("k4", "v4")
                assert "k1" not in cache_utils._cache
                assert "k4" in cache_utils._cache


class TestCacheClear:
    def test_clears_all_entries(self) -> None:
        cache_utils.cache_set("a", 1)
        cache_utils.cache_set("b", 2)
        cache_utils.cache_clear()
        assert len(cache_utils._cache) == 0
        assert cache_utils.cache_get("a", ttl=60) is None
        assert cache_utils.cache_get("b", ttl=60) is None

    def test_clear_on_empty_cache(self) -> None:
        cache_utils.cache_clear()
        assert len(cache_utils._cache) == 0
