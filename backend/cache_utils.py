"""Simple in-memory cache with TTL and eviction.

Bounds contract:
  - ``_cache`` never exceeds ``MAX_CACHE_SIZE`` entries (LRU-batch eviction).
  - Every entry carries an insertion timestamp; callers supply a TTL per lookup.
  - ``DEFAULT_CACHE_TTL`` is the recommended TTL for callers that do not have
    a domain-specific value.

Prometheus-compatible introspection:
  - ``cache_size()`` returns ``{"main": len(_cache)}`` so the API layer can
    publish ``dashboard_cache_entries{cache="main"}`` without importing this
    module's internals.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

from dashboard_config import CACHE_EVICT_BATCH, DEFAULT_CACHE_TTL, MAX_CACHE_SIZE

assert MAX_CACHE_SIZE > 0, "MAX_CACHE_SIZE must be a positive integer"  # DbC

# Global cache store: key → (value, insertion_timestamp)
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


def cache_size() -> dict[str, int]:
    """Return per-cache entry counts for Prometheus-style gauging.

    Exposes ``dashboard_cache_entries{cache="main"}`` without requiring
    the caller to reach into ``_cache`` directly.
    """
    return {"main": len(_cache)}


# Re-export for callers that want the configured default TTL.
__all__ = [
    "DEFAULT_CACHE_TTL",
    "MAX_CACHE_SIZE",
    "cache_clear",
    "cache_delete",
    "cache_get",
    "cache_set",
    "cache_size",
]
