"""Cache management utilities extracted from server.py (issue #299).

Provides:
- Bounded LRU-style cache with TTL
- Get/set operations with automatic eviction
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

# ─── Bounded Cache ────────────────────────────────────────────────────────────

MAX_CACHE_SIZE = 500
_CACHE_EVICT_BATCH = 50

# Response cache: The frontend polls every 10-15 s; without caching, each poll spawns
# dozens of `gh api` subprocesses that rapidly exhaust the 5 000 req/hr rate limit.
# TTL values are tuned to each endpoint's staleness tolerance.
#
#   runners / health  → 25 s   (runner state changes on job start/finish)
#   queue             → 20 s   (jobs drain fast; want near-real-time)
#   runs              → 30 s
#   stats             → 60 s   (aggregate counts; no need to be instant)
#   repos             → 120 s  (repo list / metadata changes rarely)
#   diagnose          → 60 s   (expensive multi-call; used for troubleshooting)
_cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()


def cache_get(key: str, ttl: float) -> Any | None:
    """Return cached value if within TTL, else None.

    Args:
        key: Cache key
        ttl: Time-to-live in seconds

    Returns:
        Cached value or None if expired/missing
    """
    entry = _cache.get(key)
    if entry is not None:
        data, ts = entry
        if time.time() - ts < ttl:
            return data
    return None


def cache_set(key: str, data: Any, _ttl: float | None = None) -> None:
    """Store value with current timestamp. Evicts oldest entries when full (issue #48).

    Args:
        key: Cache key
        data: Value to cache
        _ttl: Unused; TTL is specified at read time
    """
    if key in _cache:
        _cache.move_to_end(key)
    elif len(_cache) >= MAX_CACHE_SIZE:
        for _ in range(_CACHE_EVICT_BATCH):
            if _cache:
                _cache.popitem(last=False)
    _cache[key] = (data, time.time())


def cache_clear() -> None:
    """Clear all cached entries."""
    _cache.clear()


def cache_get_internal() -> OrderedDict[str, tuple[Any, float]]:
    """Return reference to internal cache dict (for testing/inspection)."""
    return _cache
