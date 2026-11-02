"""Last agent_bus reaper tick snapshot for operator inspection (#3484 / #3857)."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any

_lock = threading.Lock()
_last: dict[str, Any] = {
    "at_monotonic": 0.0,
    "stale_sessions": 0,
    "released_claims": 0,
    "deferred_sessions": 0,
    "errors": 0,
}


def record_reap(result: Mapping[str, int], *, deferred_sessions: int = 0, errors: int = 0) -> None:
    with _lock:
        _last.update(
            {
                "at_monotonic": time.monotonic(),
                "stale_sessions": int(result.get("stale_sessions") or 0),
                "released_claims": int(result.get("released_claims") or 0),
                "deferred_sessions": deferred_sessions,
                "errors": errors,
            }
        )


def snapshot() -> dict[str, Any]:
    with _lock:
        age_s = max(0.0, time.monotonic() - float(_last["at_monotonic"])) if _last["at_monotonic"] else None
        return {
            "last_tick_age_seconds": age_s,
            "stale_sessions": _last["stale_sessions"],
            "released_claims": _last["released_claims"],
            "deferred_sessions": _last["deferred_sessions"],
            "errors": _last["errors"],
        }


def reset_reaper_stats_for_tests() -> None:
    with _lock:
        _last.update(
            {
                "at_monotonic": 0.0,
                "stale_sessions": 0,
                "released_claims": 0,
                "deferred_sessions": 0,
                "errors": 0,
            }
        )
