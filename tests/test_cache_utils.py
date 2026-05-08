"""Tests for backend/cache_utils.py — issue #386."""

from __future__ import annotations

import cache_utils as cu
import pytest


def test_cache_get_miss_returns_none() -> None:
    c = cu.Cache("test-miss")
    assert c.get("nonexistent", ttl=10.0) is None


def test_cache_set_and_get() -> None:
    c = cu.Cache("test-set-get")
    c.set("k", {"val": 42})
    result = c.get("k", ttl=10.0)
    assert result == {"val": 42}


def test_cache_set_deep_copies() -> None:
    c = cu.Cache("test-deepcopy", deepcopy_on_set=True)
    original = {"a": 1}
    c.set("k", original)
    original["a"] = 99
    assert c.get("k", ttl=10.0)["a"] == 1


def test_cache_expired_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    c = cu.Cache("test-expire")
    c.set("k", "value")
    # Get with a tiny TTL that has already expired
    assert c.get("k", ttl=0.0) is None


def test_cache_delete() -> None:
    c = cu.Cache("test-delete")
    c.set("k", "v")
    c.delete("k")
    assert c.get("k", ttl=10.0) is None


def test_cache_clear() -> None:
    c = cu.Cache("test-clear")
    c.set("a", 1)
    c.set("b", 2)
    c.clear()
    assert c.size() == 0


def test_cache_size() -> None:
    c = cu.Cache("test-size")
    c.set("x", 1)
    c.set("y", 2)
    assert c.size() == 2


def test_cache_lru_eviction() -> None:
    """Entries are evicted when max_size is exceeded."""
    c = cu.Cache("test-lru", max_size=2, evict_batch=1)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)  # should trigger eviction
    assert c.size() <= 2


# ---------------------------------------------------------------------------
# Module-level helpers (backwards-compatible singleton API)
# ---------------------------------------------------------------------------


def test_module_cache_set_and_get() -> None:
    cu.cache_clear()
    cu.cache_set("hello", "world")
    assert cu.cache_get("hello", 10.0) == "world"


def test_module_cache_miss_returns_none() -> None:
    cu.cache_clear()
    assert cu.cache_get("missing", 10.0) is None


def test_module_cache_size_returns_dict() -> None:
    cu.cache_clear()
    cu.cache_set("a", 1)
    s = cu.cache_size()
    assert isinstance(s, dict)
    assert s.get("entries", 0) >= 1 or s.get("main", 0) >= 1 or sum(s.values()) >= 1
