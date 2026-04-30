"""In-memory cache with TTL, LRU eviction, stampede protection, and deepcopy.

Replaces four ad-hoc ``_cache: dict[str, tuple[Any, float]]`` patterns in:
- ``issue_inventory.py``
- ``linear_inventory.py``
- ``pr_inventory.py``
- ``server.py`` (``_runner_audit_cache``)

Public API
----------
Module-level (backwards-compatible, uses the singleton ``_main_cache``):
    ``cache_get(key, ttl)`` → Any | None
    ``cache_set(key, data)`` → None
    ``cache_delete(key)``    → None
    ``cache_clear()``        → None
    ``cache_size()``         → dict[str, int]

Class-level (new, per-instance isolation and stampede protection):
    ``Cache(name, max_size, deepcopy_on_set)``
    ``cache.get(key, ttl)``              → Any | None
    ``cache.set(key, data, ttl=None)``   → None
    ``cache.delete(key)``                → None
    ``cache.clear()``                    → None
    ``cache.size()``                     → int
    ``async with cache.get_or_set(key, ttl, factory)`` → Any
        (stampede-protected: concurrent misses call ``factory`` exactly once)

Design decisions
----------------
- **Max size + LRU eviction**: ``OrderedDict`` provides O(1) move_to_end;
  eviction removes the oldest ``CACHE_EVICT_BATCH`` entries.
- **Stampede protection**: per-key ``asyncio.Lock`` ensures that even under
  heavy concurrency a single-pass factory coroutine populates the cache and
  all waiters receive the same result.
- **Deep copy on set**: enabled by default for mutable values (lists, dicts)
  to prevent callers from inadvertently mutating cache contents.
- **Prometheus-ready**: ``cache_size()`` and ``Cache.size()`` return named
  counts suitable for the ``dashboard_cache_entries{cache="<name>"}`` gauge.

References: issue #363.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from dashboard_config import CACHE_EVICT_BATCH, DEFAULT_CACHE_TTL, MAX_CACHE_SIZE

log = logging.getLogger(__name__)

assert MAX_CACHE_SIZE > 0, "MAX_CACHE_SIZE must be a positive integer"  # DbC


# ---------------------------------------------------------------------------
# Cache class
# ---------------------------------------------------------------------------


class Cache:
    """Thread-safe (asyncio) in-process cache with TTL, LRU eviction, and stampede protection.

    Parameters
    ----------
    name:
        Human-readable identifier used in log messages and metrics labels.
    max_size:
        Maximum number of entries before LRU eviction kicks in.
        Defaults to ``MAX_CACHE_SIZE`` from ``dashboard_config``.
    evict_batch:
        How many entries to remove when the cache is full.
        Defaults to ``CACHE_EVICT_BATCH`` from ``dashboard_config``.
    deepcopy_on_set:
        If ``True`` (default), values are deep-copied on ``set`` so that
        mutations to the original object don't corrupt cache state.
    default_ttl:
        Default TTL (seconds) used when ``get`` is called without an explicit
        TTL. Defaults to ``DEFAULT_CACHE_TTL`` from ``dashboard_config``.
    """

    def __init__(
        self,
        name: str = "anonymous",
        *,
        max_size: int = MAX_CACHE_SIZE,
        evict_batch: int = CACHE_EVICT_BATCH,
        deepcopy_on_set: bool = True,
        default_ttl: float = DEFAULT_CACHE_TTL,
    ) -> None:
        self._name = name
        self._max_size = max_size
        self._evict_batch = evict_batch
        self._deepcopy = deepcopy_on_set
        self._default_ttl = default_ttl
        # key → (value, insertion_time_monotonic)
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        # Per-key locks for stampede protection
        self._in_flight: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()  # guards _in_flight mutations

    # ------------------------------------------------------------------
    # Core get / set / delete
    # ------------------------------------------------------------------

    def get(self, key: str, ttl: float | None = None) -> Any | None:
        """Return cached value if within TTL, else ``None``.

        Parameters
        ----------
        key:
            Cache key.
        ttl:
            Time-to-live in seconds. Defaults to ``self._default_ttl``.
        """
        if ttl is None:
            ttl = self._default_ttl
        entry = self._store.get(key)
        if entry is not None:
            data, ts = entry
            if time.monotonic() - ts < ttl:
                self._store.move_to_end(key)  # mark as recently used
                return data
            # Expired — remove proactively
            self._store.pop(key, None)
        return None

    def set(self, key: str, data: Any, ttl: float | None = None) -> None:  # noqa: A003
        """Store value. Evicts oldest entries when cache is full.

        Parameters
        ----------
        key:
            Cache key.
        data:
            Value to cache. Deep-copied if ``deepcopy_on_set`` is ``True``.
        ttl:
            Currently unused (stored timestamp is always ``now``). Reserved
            for per-entry TTL tracking in a future version.
        """
        value = copy.deepcopy(data) if self._deepcopy else data
        if key in self._store:
            self._store.move_to_end(key)
        elif len(self._store) >= self._max_size:
            for _ in range(self._evict_batch):
                if self._store:
                    evicted_key, _ = self._store.popitem(last=False)
                    log.debug("cache[%s]: evicted key=%r", self._name, evicted_key)
        self._store[key] = (value, time.monotonic())

    def delete(self, key: str) -> None:
        """Remove a single entry (no-op if absent)."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all entries."""
        self._store.clear()

    def size(self) -> int:
        """Return current number of entries."""
        return len(self._store)

    # ------------------------------------------------------------------
    # Stampede-protected async accessor
    # ------------------------------------------------------------------

    async def get_or_set(
        self,
        key: str,
        ttl: float,
        factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Return cached value or call ``factory()`` exactly once on miss.

        Under high concurrency, only the first waiter calls ``factory()``; all
        other concurrent waiters block on the per-key lock and receive the same
        cached result once the factory resolves.

        Usage::

            data = await cache.get_or_set(
                "pr_list",
                ttl=30.0,
                factory=lambda: fetch_prs_from_github(),
            )

        Parameters
        ----------
        key:
            Cache key.
        ttl:
            Time-to-live in seconds.
        factory:
            Async callable that produces the value on cache miss. Called at
            most once per concurrent group of waiters.
        """
        # Fast path — check without acquiring the meta lock
        result = self.get(key, ttl)
        if result is not None:
            return result

        # Slow path — ensure only one factory call per key
        async with self._meta_lock:
            if key not in self._in_flight:
                self._in_flight[key] = asyncio.Lock()
            key_lock = self._in_flight[key]

        async with key_lock:
            # Re-check after acquiring — another waiter may have populated it
            result = self.get(key, ttl)
            if result is not None:
                return result
            # We're the winner — call the factory
            result = await factory()
            self.set(key, result)

        # Cleanup: remove the per-key lock so it doesn't grow unboundedly
        async with self._meta_lock:
            self._in_flight.pop(key, None)

        return result


# ---------------------------------------------------------------------------
# Module-level singleton (backwards-compatible with the old cache_utils API)
# ---------------------------------------------------------------------------

_main_cache = Cache(name="main", deepcopy_on_set=False)  # keep old behaviour


def cache_get(key: str, ttl: float) -> Any | None:
    """Return cached value if within TTL, else None."""
    return _main_cache.get(key, ttl)


def cache_set(key: str, data: Any) -> None:
    """Store value with current timestamp. Evicts oldest entries when full."""
    _main_cache.set(key, data)


def cache_delete(key: str) -> None:
    """Delete a specific cache entry."""
    _main_cache.delete(key)


def cache_clear() -> None:
    """Clear all cached entries."""
    _main_cache.clear()


def cache_size() -> dict[str, int]:
    """Return per-cache entry counts for Prometheus-style gauging.

    Exposes ``dashboard_cache_entries{cache="main"}`` without requiring
    the caller to reach into ``_cache`` directly.
    """
    return {"main": _main_cache.size()}


# Re-export for callers that want the configured default TTL.
__all__ = [
    "Cache",
    "DEFAULT_CACHE_TTL",
    "MAX_CACHE_SIZE",
    "cache_clear",
    "cache_delete",
    "cache_get",
    "cache_set",
    "cache_size",
]
