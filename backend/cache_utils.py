"""Simple in-memory cache with TTL and eviction."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

from dashboard_config import CACHE_EVICT_BATCH, MAX_CACHE_SIZE

# Global cache store
_cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()


def cache_get(key: str, ttl: float) -> Any | None:
    """Return cached value if within TTL, else None."""
    entry = _cache.get(key)
    if entry is not None:
        data, ts = entry
        if time.time() - ts < ttl:
            return data
    return None


def cache_set(key: str, data: Any) -> None:
    """Store value with current timestamp. Evicts oldest entries when full."""
    if key in _cache:
        _cache.move_to_end(key)
    elif len(_cache) >= MAX_CACHE_SIZE:
        # Evict a batch to avoid constant overhead
        for _ in range(CACHE_EVICT_BATCH):
            if _cache:
                _cache.popitem(last=False)
    _cache[key] = (data, time.time())


def cache_delete(key: str) -> None:
    """Delete a specific cache entry."""
    _cache.pop(key, None)


def cache_clear() -> None:
    """Clear all cached entries."""
    _cache.clear()
