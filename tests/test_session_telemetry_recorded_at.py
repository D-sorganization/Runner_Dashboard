"""recorded_at is normalised to UTC so windows/retention compare instants, not strings."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from session_telemetry import SessionRecord, SessionTelemetryStore  # noqa: E402


@pytest.fixture
def temp_store(tmp_path: Path) -> SessionTelemetryStore:
    return SessionTelemetryStore(data_file=tmp_path / "telemetry.json")


def test_recorded_at_with_offset_is_normalised_to_utc() -> None:
    rec = SessionRecord.from_dict({"session_id": "s", "recorded_at": "2026-11-16T12:00:00+05:00"})
    assert datetime.fromisoformat(rec.recorded_at) == datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
    assert rec.recorded_at.endswith("+00:00")


def test_recorded_at_z_suffix_and_naive_are_treated_as_utc() -> None:
    z = SessionRecord.from_dict({"session_id": "a", "recorded_at": "2026-11-16T07:00:00Z"})
    naive = SessionRecord.from_dict({"session_id": "b", "recorded_at": "2026-11-16T07:00:00"})
    expected = datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
    assert datetime.fromisoformat(z.recorded_at) == expected
    assert datetime.fromisoformat(naive.recorded_at) == expected


def test_recorded_at_unparseable_falls_back_to_now() -> None:
    before = datetime.now(UTC)
    rec = SessionRecord.from_dict({"session_id": "s", "recorded_at": "yesterday-ish"})
    assert before <= datetime.fromisoformat(rec.recorded_at) <= datetime.now(UTC)


def test_metrics_window_places_offset_timestamps_by_instant(temp_store: SessionTelemetryStore) -> None:
    # 12:00+05:00 is 07:00Z: inside [2026-11-15T10:00Z, 2026-11-16T10:00Z).
    # A lexical compare against "...T10:00:00+00:00" wrongly excludes it.
    temp_store.record_session(
        {"session_id": "in", "cost_usd": 1.0, "pr_merged": True, "recorded_at": "2026-11-16T12:00:00+05:00"}
    )
    # 09:30-02:00 is 11:30Z: after the window end, but lexically "09:30" < "10:00".
    temp_store.record_session(
        {"session_id": "out", "cost_usd": 100.0, "pr_merged": True, "recorded_at": "2026-11-16T09:30:00-02:00"}
    )
    now = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
    metrics = temp_store.get_metrics_window(days=1, now=now)
    assert metrics["total_sessions"] == 1
    assert metrics["cost_per_merged_pr"]["median_usd"] == 1.0
