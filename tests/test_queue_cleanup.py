"""Tests for backend/queue_cleanup.py — issue #386."""

from __future__ import annotations

import datetime as _dt

import queue_cleanup as qc

UTC = getattr(_dt, "UTC", _dt.UTC)


# ---------------------------------------------------------------------------
# StaleRun dataclass
# ---------------------------------------------------------------------------


def test_stale_run_as_dict() -> None:
    run = qc.StaleRun(
        repo="my-repo",
        run_id=12345,
        workflow="CI",
        branch="main",
        created_at="2026-04-01T10:00:00Z",
        age_minutes=90,
    )
    d = run.as_dict()
    assert d["repo"] == "my-repo"
    assert d["run_id"] == 12345
    assert d["age_minutes"] == 90
    assert d["cancelled"] is False


def test_stale_run_cancelled_field() -> None:
    run = qc.StaleRun(
        repo="r",
        run_id=1,
        workflow="w",
        branch="b",
        created_at="2026-04-01T10:00:00Z",
        age_minutes=120,
        cancelled=True,
        cancel_error="",
    )
    assert run.cancelled is True


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_default_min_age_minutes_reasonable() -> None:
    assert qc.DEFAULT_MIN_AGE_MINUTES > 0


def test_scan_concurrency_positive() -> None:
    assert qc._SCAN_CONCURRENCY > 0


def test_cancel_concurrency_positive() -> None:
    assert qc._CANCEL_CONCURRENCY > 0
