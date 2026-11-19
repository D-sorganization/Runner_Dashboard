"""Stale-while-revalidate cache for ``GET /api/fleet/status`` (#2110, #4576)."""

from __future__ import annotations

import copy
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from cache_utils import cache_get, cache_get_swr
from dashboard_config import HttpTimeout

FLEET_STATUS_FRESH_TTL_S = float(os.environ.get("RUNNER_DASHBOARD_FLEET_STATUS_FRESH_TTL_S", "10"))
FLEET_STATUS_STALE_TTL_S = float(os.environ.get("RUNNER_DASHBOARD_FLEET_STATUS_STALE_TTL_S", "120"))
FLEET_STATUS_WAIT_TIMEOUT_S = float(
    os.environ.get("RUNNER_DASHBOARD_FLEET_STATUS_WAIT_S", str(HttpTimeout.FLEET_NODE_PROBE_READ_S + 1.0))
)
# Host telemetry age, independent of the aggregate cache window. A payload may be
# served with ``_cache_stale`` while a host sampled inside this window stays fresh.
HOST_TELEMETRY_STALE_AFTER_S = float(os.environ.get("RUNNER_DASHBOARD_HOST_TELEMETRY_STALE_S", "90"))

FLEET_STATUS_CACHE_PREFIX = "fleet:status:v1"


def fleet_status_cache_key(*, exclude_pools: bool) -> str:
    return f"{FLEET_STATUS_CACHE_PREFIX}:exclude_pools={int(exclude_pools)}"


def _parse_sampled_at(raw: object) -> datetime | None:
    """Return an aware UTC instant, or ``None`` when the sample time cannot be trusted."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _format_sampled_at(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def with_host_telemetry_freshness(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    stale_after_s: float | None = None,
) -> dict[str, Any]:
    """Copy ``payload`` and set ``stale`` plus ``_sampled_at`` on each host record.

    Preconditions: ``payload`` is the fleet-status map. Keys starting with ``_`` are
    metadata, not hosts. ``now`` is timezone-aware when provided. ``stale_after_s`` is
    ``>= 0`` when provided.

    Postconditions: the result does not alias ``payload`` or its nested host dicts.
    Each host dict has ``stale`` (bool) and ``_sampled_at`` (UTC ``Z`` string or
    ``None``). A host is stale when its sample time is missing, naive, unparseable,
    or older than the window. Metadata keys and non-dict values are unchanged.
    """
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    limit = HOST_TELEMETRY_STALE_AFTER_S if stale_after_s is None else stale_after_s
    if limit < 0:
        raise ValueError("stale_after_s must be >= 0")
    out = copy.deepcopy(payload)
    for key, value in out.items():
        if str(key).startswith("_") or not isinstance(value, dict):
            continue
        sampled = _parse_sampled_at(value.get("_sampled_at"))
        if sampled is None:
            sampled = _parse_sampled_at(value.get("timestamp"))
        if sampled is None:
            value["stale"] = True
            value["_sampled_at"] = None
            continue
        value["_sampled_at"] = _format_sampled_at(sampled)
        value["stale"] = (moment - sampled).total_seconds() > limit
    return out


def _finalize_fleet_status(
    payload: dict[str, Any],
    *,
    cache_stale: bool,
    now: datetime | None,
) -> dict[str, Any]:
    """Attach per-host freshness and the aggregate cache flag.

    ``_cache_stale`` names cache age only. The legacy ``_stale`` key is removed so
    consumers do not treat a cache hit as every host being unknown.
    """
    body = with_host_telemetry_freshness(payload, now=now)
    body.pop("_stale", None)
    if cache_stale:
        body["_cache_stale"] = True
    else:
        body.pop("_cache_stale", None)
    return body


async def serve_fleet_status(
    cache_key: str,
    collect: Callable[[], Awaitable[dict[str, Any]]],
    *,
    wait_timeout: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return fleet status fresh, stale-while-revalidate, or a bounded cold wait.

    Postconditions: every host record has ``stale`` and ``_sampled_at``.
    ``_cache_stale`` is present only when the aggregate body was served from cache
    outside the fresh TTL (or the cold collect timed out). ``_stale`` is absent.
    """
    wait = FLEET_STATUS_WAIT_TIMEOUT_S if wait_timeout is None else wait_timeout
    fresh = cache_get(cache_key, FLEET_STATUS_FRESH_TTL_S)
    if fresh is not None:
        return _finalize_fleet_status(fresh, cache_stale=False, now=now)

    stale_before = cache_get(cache_key, FLEET_STATUS_STALE_TTL_S)

    def on_timeout() -> dict[str, Any]:
        stale = cache_get(cache_key, FLEET_STATUS_STALE_TTL_S) or stale_before
        if stale is not None:
            return {**copy.deepcopy(stale), "_cache_stale": True}
        return {"_cache_stale": True, "_warming": True}

    result = await cache_get_swr(
        cache_key,
        collect,
        fresh_ttl=FLEET_STATUS_FRESH_TTL_S,
        stale_ttl=FLEET_STATUS_STALE_TTL_S,
        wait_timeout=wait,
        on_timeout=on_timeout,
    )

    if stale_before is not None and cache_get(cache_key, FLEET_STATUS_FRESH_TTL_S) is None:
        return _finalize_fleet_status(result, cache_stale=True, now=now)
    return _finalize_fleet_status(result, cache_stale=bool(result.get("_cache_stale")), now=now)
