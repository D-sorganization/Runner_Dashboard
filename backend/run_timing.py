"""Pure functions for computing per-run queue-wait vs. execution timing.

No FastAPI or identity imports — keeps this module importable from tests
without triggering the principals.yml security check in identity.py.

These helpers are consumed by ``backend/routers/queue.py`` for the
``GET /api/queue/status`` endpoint (issue #637).
"""

from __future__ import annotations

from datetime import UTC, datetime


def parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string into a UTC-aware datetime.

    Returns ``None`` if the input is absent or unparseable.
    GitHub timestamps are always UTC (end in ``Z`` or ``+00:00``).
    """
    if not ts:
        return None
    try:
        # Python 3.11+ handles ``Z`` natively; fromisoformat on 3.10 does not.
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def compute_run_timing(run: dict) -> dict:
    """Compute per-run queue-wait and execution timing from GitHub timestamps.

    GitHub provides:
      - ``created_at``    — when the run was created (queued).
      - ``run_started_at``— when the first job began executing on a runner.

    For *in-progress* runs:
      queue_wait_seconds = run_started_at - created_at
      exec_seconds       = now            - run_started_at

    For *queued* runs (no runner assigned yet):
      queue_wait_seconds = now - created_at
      exec_seconds       = 0

    All values are non-negative integers (clamped to 0 on clock skew).

    Returns::

        {"queue_wait_seconds": int, "exec_seconds": int}
    """
    now = datetime.now(UTC)
    created = parse_iso(run.get("created_at"))
    started = parse_iso(run.get("run_started_at"))

    if started is not None and created is not None:
        queue_wait = max(0, int((started - created).total_seconds()))
        exec_secs = max(0, int((now - started).total_seconds()))
    elif created is not None:
        # Still queued — no runner yet.
        queue_wait = max(0, int((now - created).total_seconds()))
        exec_secs = 0
    else:
        queue_wait = 0
        exec_secs = 0

    return {
        "queue_wait_seconds": queue_wait,
        "exec_seconds": exec_secs,
    }


def annotate_runs_with_timing(queue_data: dict) -> dict:
    """Return a copy of *queue_data* with ``timing`` added to every run.

    The ``timing`` key holds the dict returned by :func:`compute_run_timing`.
    Original run dicts are not mutated.

    Example::

        >>> raw = {"in_progress": [{"id": 1, "run_started_at": "..."}], "queued": []}
        >>> result = annotate_runs_with_timing(raw)
        >>> result["in_progress"][0]["timing"]
        {"queue_wait_seconds": 45, "exec_seconds": 120}
    """
    result = dict(queue_data)
    result["in_progress"] = [{**run, "timing": compute_run_timing(run)} for run in queue_data.get("in_progress", [])]
    result["queued"] = [{**run, "timing": compute_run_timing(run)} for run in queue_data.get("queued", [])]
    return result
