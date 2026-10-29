"""Smoke tests for backend/activity_transcript.py (#2876)."""

from __future__ import annotations

import activity_transcript as at


def test_activity_metrics_snapshot_returns_dict() -> None:
    at.reset_activity_metrics()
    snapshot = at.activity_metrics_snapshot()
    assert isinstance(snapshot, dict)
    assert "ingest_events" in snapshot
