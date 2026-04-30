"""Tests proving that cache_utils and _cpu_history stay bounded — issue #435.

Acceptance criteria verified:
  AC#1 - cache_utils declares max_size (MAX_CACHE_SIZE) and per-entry TTL.
  AC#2 - _cpu_history is a deque with an explicit maxlen cap.
  AC#3 - This test file (eviction under load).
  AC#4 - cache_size() returns per-cache gauge data.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import cache_utils  # noqa: E402


@pytest.fixture(autouse=True)
def _clear():
    """Guarantee a clean cache for every test."""
    cache_utils.cache_clear()
    yield
    cache_utils.cache_clear()


# ---------------------------------------------------------------------------
# AC#1  cache declares max_size
# ---------------------------------------------------------------------------


class TestMaxSizeDeclared:
    def test_max_cache_size_positive(self) -> None:
        """MAX_CACHE_SIZE must be a positive integer (DbC)."""
        assert cache_utils.MAX_CACHE_SIZE > 0

    def test_max_cache_size_imported_from_config(self) -> None:
        """MAX_CACHE_SIZE must match dashboard_config so there is one source of truth."""
        import dashboard_config  # noqa: PLC0415

        assert cache_utils.MAX_CACHE_SIZE == dashboard_config.MAX_CACHE_SIZE

    def test_default_cache_ttl_positive(self) -> None:
        """DEFAULT_CACHE_TTL must be a positive float."""
        assert cache_utils.DEFAULT_CACHE_TTL > 0

    def test_default_cache_ttl_from_config(self) -> None:
        """DEFAULT_CACHE_TTL must match dashboard_config."""
        import dashboard_config  # noqa: PLC0415

        assert cache_utils.DEFAULT_CACHE_TTL == dashboard_config.DEFAULT_CACHE_TTL


# ---------------------------------------------------------------------------
# AC#2  _cpu_history is bounded
# ---------------------------------------------------------------------------


class TestCpuHistoryBounded:
    def test_routers_system_cpu_history_has_maxlen(self) -> None:
        """_cpu_history in routers/system.py must be a deque with maxlen set."""
        from routers.system import _cpu_history  # noqa: PLC0415

        assert isinstance(_cpu_history, deque)
        assert _cpu_history.maxlen is not None
        assert _cpu_history.maxlen > 0

    def test_system_utils_cpu_history_has_maxlen(self) -> None:
        """_cpu_history in system_utils.py must be a deque with maxlen set."""
        import system_utils  # noqa: PLC0415

        assert isinstance(system_utils._cpu_history, deque)
        assert system_utils._cpu_history.maxlen is not None
        assert system_utils._cpu_history.maxlen > 0

    def test_cpu_history_does_not_grow_beyond_maxlen(self) -> None:
        """Appending more samples than maxlen must not grow the deque."""
        from routers.system import _cpu_history  # noqa: PLC0415

        cap = _cpu_history.maxlen
        assert cap is not None
        for i in range(cap * 3):
            _cpu_history.append(float(i % 100))
        assert len(_cpu_history) <= cap

    def test_cpu_history_maxlen_matches_config(self) -> None:
        """maxlen must equal CPU_HISTORY_MAXLEN from dashboard_config."""
        import dashboard_config  # noqa: PLC0415
        from routers.system import _cpu_history  # noqa: PLC0415

        assert _cpu_history.maxlen == dashboard_config.CPU_HISTORY_MAXLEN


# ---------------------------------------------------------------------------
# AC#3  eviction under load — cache never exceeds cap
# ---------------------------------------------------------------------------


class TestCacheEviction:
    def test_cache_stays_at_or_below_max_size(self) -> None:
        """Filling the cache 3× beyond MAX_CACHE_SIZE must never exceed the cap."""
        cap = cache_utils.MAX_CACHE_SIZE
        for i in range(cap * 3):
            cache_utils.cache_set(f"key:{i}", i)
        assert len(cache_utils._cache) <= cap

    def test_eviction_removes_oldest_first(self) -> None:
        """With CACHE_EVICT_BATCH=1 and MAX_CACHE_SIZE=3, the oldest key must go first."""
        with patch.object(cache_utils, "MAX_CACHE_SIZE", 3), patch.object(cache_utils, "CACHE_EVICT_BATCH", 1):
            cache_utils.cache_clear()
            cache_utils.cache_set("a", 1)
            cache_utils.cache_set("b", 2)
            cache_utils.cache_set("c", 3)
            # Adding a 4th item must evict "a" (the oldest)
            cache_utils.cache_set("d", 4)
            assert "a" not in cache_utils._cache
            assert "d" in cache_utils._cache

    def test_update_existing_key_does_not_grow_cache(self) -> None:
        """Updating an existing key must not increase cache length."""
        cache_utils.cache_set("x", 1)
        size_before = len(cache_utils._cache)
        cache_utils.cache_set("x", 2)
        assert len(cache_utils._cache) == size_before

    def test_cache_size_at_exact_cap(self) -> None:
        """Cache at exactly MAX_CACHE_SIZE must accept no more entries without evicting."""
        cap = cache_utils.MAX_CACHE_SIZE
        for i in range(cap):
            cache_utils.cache_set(f"k{i}", i)
        assert len(cache_utils._cache) == cap
        # One more: eviction must fire, keeping size <= cap
        cache_utils.cache_set("overflow", "boom")
        assert len(cache_utils._cache) <= cap


# ---------------------------------------------------------------------------
# AC#4  cache_size() exposes per-cache gauge data
# ---------------------------------------------------------------------------


class TestCacheSizeGauge:
    def test_cache_size_returns_dict(self) -> None:
        """cache_size() must return a dict."""
        result = cache_utils.cache_size()
        assert isinstance(result, dict)

    def test_cache_size_has_main_key(self) -> None:
        """cache_size() must contain the 'main' cache key."""
        result = cache_utils.cache_size()
        assert "main" in result

    def test_cache_size_reflects_current_entries(self) -> None:
        """cache_size()['main'] must equal the actual number of cached entries."""
        cache_utils.cache_clear()
        assert cache_utils.cache_size()["main"] == 0
        cache_utils.cache_set("p", 1)
        cache_utils.cache_set("q", 2)
        assert cache_utils.cache_size()["main"] == 2

    def test_cache_size_decreases_after_delete(self) -> None:
        """cache_size() must decrease after cache_delete()."""
        cache_utils.cache_set("r", 3)
        before = cache_utils.cache_size()["main"]
        cache_utils.cache_delete("r")
        assert cache_utils.cache_size()["main"] == before - 1

    def test_cache_size_zero_after_clear(self) -> None:
        """cache_size() must return 0 for all caches after cache_clear()."""
        cache_utils.cache_set("s", 4)
        cache_utils.cache_clear()
        for count in cache_utils.cache_size().values():
            assert count == 0
