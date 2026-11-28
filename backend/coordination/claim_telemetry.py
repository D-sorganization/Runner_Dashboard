"""Claims-path telemetry and overnight baseline recorder (issue #4595).

One process-local, thread-safe counter set for the Desk hub claims path:

* ``record_claim_check``      — one ``GET /api/coordination/claims`` completion
  (duration + result), feeding the p50/p95 reservoir.
* ``record_rm_subprocess``    — one Repository_Management script subprocess
  (``check_agent_claim`` / ``post_agent_lease`` / ``release_agent_lease`` /
  ``agent_communicate``): the hub-visible proxy for GitHub calls per request.
* ``record_blocked_exit``     — a lane could not proceed (held, timeout,
  request budget exceeded).
* ``record_expired_hold``     — a held claim whose ``expires_at`` is in the past.

When ``CLAIMS_BASELINE_FILE`` is set, every record is also appended as one JSON
line so an overnight run can be written up without a metrics server. ``summary``
returns the in-process p50/p95 and counters; ``scripts/claims_baseline.py``
turns the JSONL into the write-up.

DbC: every recorder validates its inputs and raises ``ValueError`` on a bad
call; a well-formed call never raises, and the JSONL append is best-effort so
telemetry can never break the claims path it observes.
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections import deque
from typing import Any

from time_utils import utc_now_iso

RESULT_KINDS = frozenset({"available", "held", "unavailable", "stale", "unavailable_budget"})
_RESERVOIR_MAXLEN = 20_000
_BASELINE_ENV = "CLAIMS_BASELINE_FILE"

_lock = threading.Lock()
_durations: deque[float] = deque(maxlen=_RESERVOIR_MAXLEN)
_by_result: dict[str, int] = {}
_rm_calls: dict[str, int] = {}
_rm_timeouts: dict[str, int] = {}
_blocked_exits: dict[str, int] = {}
_expired_holds_count = 0
_expired_holds_max_age = 0.0


def reset() -> None:
    """Forget all in-process samples and counters (tests)."""
    global _expired_holds_count, _expired_holds_max_age
    with _lock:
        _durations.clear()
        _by_result.clear()
        _rm_calls.clear()
        _rm_timeouts.clear()
        _blocked_exits.clear()
        _expired_holds_count = 0
        _expired_holds_max_age = 0.0


def _append_jsonl(kind: str, payload: dict[str, Any]) -> None:
    path = os.environ.get(_BASELINE_ENV)
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": kind, "at": utc_now_iso(), **payload}) + "\n")
    except OSError:
        # A full/locked baseline file must never break the claims path.
        pass


def record_claim_check(duration_s: float, result: str) -> None:
    """Record one claims-path read: its wall duration and outcome kind.

    Pre: ``duration_s >= 0`` and ``result`` is one of ``RESULT_KINDS``.
    """
    if duration_s < 0.0:
        raise ValueError("claim check duration must be non-negative")
    if result not in RESULT_KINDS:
        raise ValueError(f"unknown claim check result: {result!r}")
    with _lock:
        _durations.append(float(duration_s))
        _by_result[result] = _by_result.get(result, 0) + 1
    _append_jsonl("claim_check", {"result": result, "duration_s": round(float(duration_s), 4)})


def record_rm_subprocess(script: str, duration_s: float, *, timed_out: bool) -> None:
    """Record one RM script subprocess — the hub's GitHub-call proxy on the claims path."""
    if not script or not isinstance(script, str):
        raise ValueError("script name must be a non-empty string")
    if duration_s < 0.0:
        raise ValueError("subprocess duration must be non-negative")
    with _lock:
        _rm_calls[script] = _rm_calls.get(script, 0) + 1
        if timed_out:
            _rm_timeouts[script] = _rm_timeouts.get(script, 0) + 1
    if timed_out:
        record_blocked_exit(f"timeout:{script}")
    _append_jsonl(
        "rm_subprocess",
        {"script": script, "duration_s": round(float(duration_s), 4), "timed_out": bool(timed_out)},
    )


def record_blocked_exit(reason: str) -> None:
    """Record a lane exit the claims path blocked (held / timeout / budget exceeded)."""
    if not reason or not isinstance(reason, str):
        raise ValueError("blocked exit reason must be a non-empty string")
    with _lock:
        _blocked_exits[reason] = _blocked_exits.get(reason, 0) + 1
    _append_jsonl("blocked_exit", {"reason": reason})


def record_expired_hold(age_s: float) -> None:
    """Record a held claim whose ``expires_at`` is ``age_s`` in the past."""
    if age_s < 0.0:
        raise ValueError("expired hold age must be non-negative")
    global _expired_holds_count, _expired_holds_max_age
    with _lock:
        _expired_holds_count += 1
        _expired_holds_max_age = max(_expired_holds_max_age, float(age_s))
    _append_jsonl("expired_hold", {"age_s": round(float(age_s), 3)})


def _percentile(sorted_values: list[float], percentile: float) -> float | None:
    """Nearest-rank percentile, or ``None`` when there are no samples."""
    if not sorted_values:
        return None
    index = max(0, int(math.ceil(percentile / 100.0 * len(sorted_values))) - 1)
    return sorted_values[index]


def summary() -> dict[str, Any]:
    """In-process snapshot: p50/p95 of claim-check latency plus every counter."""
    with _lock:
        values = sorted(_durations)
        by_result = dict(_by_result)
        rm_calls = dict(_rm_calls)
        rm_timeouts = dict(_rm_timeouts)
        blocked_exits = dict(_blocked_exits)
        expired_holds_count = _expired_holds_count
        expired_holds_max_age = _expired_holds_max_age
    return {
        "claims_requests_total": len(values),
        "p50_s": _percentile(values, 50.0),
        "p95_s": _percentile(values, 95.0),
        "by_result": by_result,
        "rm_subprocess_calls": rm_calls,
        "rm_subprocess_timeouts": rm_timeouts,
        "blocked_exits": blocked_exits,
        "expired_holds_count": expired_holds_count,
        "expired_holds_max_age_s": round(expired_holds_max_age, 3) if expired_holds_count else None,
    }
