"""Tests for per-run queue-wait vs. execution-time timing helpers (issue #637).

Tests the pure-Python ``run_timing`` module (no FastAPI/identity imports)
and verifies the ``GET /api/queue/status`` route is registered on the queue
router.

Covers:
  - parse_iso: parses GitHub-style ISO-8601 timestamps (Z-suffix and +00:00).
  - compute_run_timing: returns correct queue_wait_seconds / exec_seconds for
    both in-progress and queued runs; handles missing/bad timestamps.
  - annotate_runs_with_timing: annotates every run in both lists; does not
    mutate the original run dicts.
  - GET /api/queue/status route is registered and accepts only GET.
  - routers/queue.py imports from run_timing (DRY enforcement).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

import run_timing as rt  # no identity / FastAPI deps


# ---------------------------------------------------------------------------
# parse_iso
# ---------------------------------------------------------------------------


def test_parse_iso_z_suffix() -> None:
    result = rt.parse_iso("2026-05-17T12:00:00Z")
    assert result is not None
    assert result.tzinfo is not None
    assert result.year == 2026
    assert result.month == 5
    assert result.day == 17
    assert result.hour == 12


def test_parse_iso_plus_zero() -> None:
    result = rt.parse_iso("2026-05-17T12:00:00+00:00")
    assert result is not None
    assert result.hour == 12


def test_parse_iso_none_returns_none() -> None:
    assert rt.parse_iso(None) is None


def test_parse_iso_empty_string_returns_none() -> None:
    assert rt.parse_iso("") is None


def test_parse_iso_invalid_string_returns_none() -> None:
    assert rt.parse_iso("not-a-date") is None


# ---------------------------------------------------------------------------
# compute_run_timing — in-progress run (has run_started_at)
# ---------------------------------------------------------------------------


def test_compute_run_timing_in_progress() -> None:
    """Queue wait = started_at - created_at; exec = now - started_at."""
    now = datetime(2026, 5, 17, 14, 0, 0, tzinfo=UTC)
    created = datetime(2026, 5, 17, 13, 55, 0, tzinfo=UTC)  # 5 min before started
    started = datetime(2026, 5, 17, 13, 57, 0, tzinfo=UTC)  # 3 min before now

    run = {
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "run_started_at": started.isoformat().replace("+00:00", "Z"),
    }

    with patch.object(rt, "datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat
        timing = rt.compute_run_timing(run)

    assert timing["queue_wait_seconds"] == 120  # 2 min (started - created)
    assert timing["exec_seconds"] == 180  # 3 min (now - started)


def test_compute_run_timing_queued_no_started_at() -> None:
    """Queued run: exec_seconds=0, queue_wait = now - created_at."""
    now = datetime(2026, 5, 17, 14, 0, 0, tzinfo=UTC)
    created = datetime(2026, 5, 17, 13, 50, 0, tzinfo=UTC)  # 10 min ago

    run = {
        "created_at": created.isoformat().replace("+00:00", "Z"),
    }

    with patch.object(rt, "datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat
        timing = rt.compute_run_timing(run)

    assert timing["queue_wait_seconds"] == 600  # 10 min
    assert timing["exec_seconds"] == 0


def test_compute_run_timing_missing_timestamps() -> None:
    """Missing created_at: both values are 0 (no data to compute from)."""
    timing = rt.compute_run_timing({})
    assert timing["queue_wait_seconds"] == 0
    assert timing["exec_seconds"] == 0


def test_compute_run_timing_clamps_negative_values() -> None:
    """Clock skew should not produce negative values."""
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    run = {
        "created_at": future,  # future timestamp → negative diff → clamped to 0
    }
    timing = rt.compute_run_timing(run)
    assert timing["queue_wait_seconds"] >= 0
    assert timing["exec_seconds"] >= 0


# ---------------------------------------------------------------------------
# annotate_runs_with_timing
# ---------------------------------------------------------------------------


def test_annotate_runs_adds_timing_key_to_in_progress() -> None:
    raw = {
        "in_progress": [{"id": 1, "created_at": "2026-05-17T12:00:00Z"}],
        "queued": [],
    }
    result = rt.annotate_runs_with_timing(raw)
    assert "timing" in result["in_progress"][0]
    assert isinstance(result["in_progress"][0]["timing"]["queue_wait_seconds"], int)


def test_annotate_runs_adds_timing_key_to_queued() -> None:
    raw = {
        "in_progress": [],
        "queued": [{"id": 2, "created_at": "2026-05-17T12:00:00Z"}],
    }
    result = rt.annotate_runs_with_timing(raw)
    assert "timing" in result["queued"][0]
    assert result["queued"][0]["timing"]["exec_seconds"] == 0


def test_annotate_runs_does_not_mutate_original() -> None:
    original_run = {"id": 1, "created_at": "2026-05-17T12:00:00Z"}
    raw = {
        "in_progress": [original_run],
        "queued": [],
    }
    rt.annotate_runs_with_timing(raw)
    assert "timing" not in original_run, "original run dict must not be mutated"


def test_annotate_runs_preserves_extra_queue_fields() -> None:
    raw = {
        "in_progress": [],
        "queued": [],
        "total": 5,
        "stats": {"repos_sampled": 10},
    }
    result = rt.annotate_runs_with_timing(raw)
    assert result["total"] == 5
    assert result["stats"]["repos_sampled"] == 10


def test_annotate_runs_empty_lists() -> None:
    raw = {"in_progress": [], "queued": []}
    result = rt.annotate_runs_with_timing(raw)
    assert result["in_progress"] == []
    assert result["queued"] == []


# ---------------------------------------------------------------------------
# Route registration — source-level inspection (avoids identity.py side-effects)
# ---------------------------------------------------------------------------


def test_queue_status_route_declared_in_source() -> None:
    """GET /api/queue/status must be declared with @router.get in queue.py."""
    source = (_BACKEND_DIR / "routers" / "queue.py").read_text(encoding="utf-8")
    assert '@router.get("/api/queue/status")' in source, "queue.py must declare @router.get('/api/queue/status')"


def test_queue_status_calls_annotate_runs_with_timing() -> None:
    """get_queue_status must delegate to annotate_runs_with_timing from run_timing."""
    source = (_BACKEND_DIR / "routers" / "queue.py").read_text(encoding="utf-8")
    assert "annotate_runs_with_timing" in source, "queue.py must call annotate_runs_with_timing"


def test_queue_router_imports_run_timing() -> None:
    """queue.py must import from run_timing (DRY — no duplicated timing logic)."""
    source = (_BACKEND_DIR / "routers" / "queue.py").read_text(encoding="utf-8")
    assert "from run_timing import" in source or "import run_timing" in source, (
        "queue.py must delegate timing to run_timing module"
    )
