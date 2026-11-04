"""Hermetic tests for the disk-fill/anomaly trend and projection library (#3934 S1)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from disk_fill_trend import (  # noqa: E402
    Baseline,
    Sample,
    is_anomalous,
    project_disk_fill,
    update_baseline,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def test_update_baseline_seeds_from_first_sample() -> None:
    baseline = update_baseline(None, host="desk-1", metric="disk_free_bytes", value=100.0)

    assert baseline.host == "desk-1"
    assert baseline.metric == "disk_free_bytes"
    assert baseline.mean == 100.0
    assert baseline.deviation == 0.0
    assert baseline.sample_count == 1


def test_update_baseline_tracks_ewma_mean_and_deviation() -> None:
    baseline = update_baseline(None, host="desk-1", metric="queue_depth", value=10.0)
    baseline = update_baseline(baseline, host="desk-1", metric="queue_depth", value=20.0, alpha=0.5)

    # EWMA mean moves halfway toward the new value at alpha=0.5.
    assert baseline.mean == pytest.approx(15.0)
    assert baseline.deviation > 0.0
    assert baseline.sample_count == 2


def test_update_baseline_rejects_mismatched_host_or_metric() -> None:
    baseline = update_baseline(None, host="desk-1", metric="queue_depth", value=10.0)

    with pytest.raises(ValueError):
        update_baseline(baseline, host="desk-2", metric="queue_depth", value=10.0)

    with pytest.raises(ValueError):
        update_baseline(baseline, host="desk-1", metric="run_failures", value=10.0)


def test_update_baseline_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        update_baseline(None, host="desk-1", metric="queue_depth", value=float("nan"))

    with pytest.raises(ValueError):
        update_baseline(None, host="desk-1", metric="queue_depth", value=float("inf"))


def test_is_anomalous_flags_values_far_from_baseline() -> None:
    baseline = Baseline(host="desk-1", metric="run_failures", mean=2.0, deviation=0.5, sample_count=20)

    assert is_anomalous(baseline, 2.2) is False
    assert is_anomalous(baseline, 10.0) is True


def test_is_anomalous_requires_minimum_sample_count() -> None:
    baseline = Baseline(host="desk-1", metric="run_failures", mean=2.0, deviation=0.5, sample_count=2)

    # Too few samples to trust the baseline yet: never flags, however extreme.
    assert is_anomalous(baseline, 1000.0) is False


def test_is_anomalous_handles_zero_deviation_without_division_error() -> None:
    baseline = Baseline(host="desk-1", metric="disk_free_bytes", mean=500.0, deviation=0.0, sample_count=10)

    assert is_anomalous(baseline, 500.0) is False
    assert is_anomalous(baseline, 50.0) is True


def _samples(values_by_minutes_ago: list[tuple[int, float]]) -> list[Sample]:
    return [
        Sample(timestamp=_NOW - timedelta(minutes=minutes_ago), value=value)
        for minutes_ago, value in values_by_minutes_ago
    ]


def test_project_disk_fill_alerts_when_shrinking_under_threshold() -> None:
    # Disk free drops from 100 GiB to 40 GiB over 60 minutes -> ~40h left at this
    # rate, but shrinking fast enough across a shorter window below should alert.
    samples = _samples([(120, 100_000.0), (60, 40_000.0), (0, 10_000.0)])

    projection = project_disk_fill("desk-1", samples)

    assert projection.host == "desk-1"
    assert projection.hours_to_full is not None
    assert projection.hours_to_full < 24.0
    assert projection.alert is True


def test_project_disk_fill_no_alert_when_margin_exceeds_threshold() -> None:
    samples = _samples([(120, 100_000.0), (60, 99_000.0), (0, 98_000.0)])

    projection = project_disk_fill("desk-1", samples)

    assert projection.hours_to_full is not None
    assert projection.hours_to_full > 24.0
    assert projection.alert is False


def test_project_disk_fill_no_projection_when_disk_is_growing() -> None:
    samples = _samples([(120, 50_000.0), (60, 60_000.0), (0, 70_000.0)])

    projection = project_disk_fill("desk-1", samples)

    assert projection.hours_to_full is None
    assert projection.alert is False


def test_project_disk_fill_no_projection_when_flat() -> None:
    samples = _samples([(120, 50_000.0), (60, 50_000.0), (0, 50_000.0)])

    projection = project_disk_fill("desk-1", samples)

    assert projection.hours_to_full is None
    assert projection.alert is False


def test_project_disk_fill_requires_at_least_two_samples() -> None:
    with pytest.raises(ValueError):
        project_disk_fill("desk-1", _samples([(0, 50_000.0)]))


def test_project_disk_fill_honors_custom_threshold() -> None:
    samples = _samples([(120, 100_000.0), (60, 90_000.0), (0, 80_000.0)])

    projection = project_disk_fill("desk-1", samples, threshold_hours=72.0)

    assert projection.hours_to_full is not None
    assert projection.alert is True
