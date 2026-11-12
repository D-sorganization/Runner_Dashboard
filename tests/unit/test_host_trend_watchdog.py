"""Tests for the fleet health watchdog metrics bridge (#3934 S2).

Bridges the S1 EWMA/disk-fill library (``disk_fill_trend``) into a stateful,
per-process registry that accumulates samples across polls and renders
Prometheus exposition lines.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from host_trend_watchdog import (  # noqa: E402
    HostTrendWatchdog,
    render_watchdog_lines,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def test_observe_seeds_baseline_without_alert() -> None:
    watchdog = HostTrendWatchdog()
    rows = watchdog.observe(
        {"desk-1": {"disk_free_bytes": 500_000_000_000}},
        now=_NOW,
    )
    row = rows[0]
    assert row["host"] == "desk-1"
    assert row["disk_free_bytes"] == 500_000_000_000
    assert row["anomaly"] is False
    assert row["hours_to_full"] is None
    assert row["disk_alert"] is False


def test_observe_flags_disk_fill_alert_from_shrinking_trend() -> None:
    watchdog = HostTrendWatchdog()
    # Disk free drops by 100 GB/hour; starting at 1000 GB it will hit zero in 10h.
    free_bytes = 1_000_000_000_000
    for i in range(4):
        watchdog.observe(
            {"desk-1": {"disk_free_bytes": free_bytes - i * 100_000_000_000}},
            now=_NOW + timedelta(hours=i),
        )
    rows = watchdog.observe(
        {"desk-1": {"disk_free_bytes": free_bytes - 4 * 100_000_000_000}},
        now=_NOW + timedelta(hours=4),
    )
    row = rows[0]
    assert row["hours_to_full"] is not None
    assert row["hours_to_full"] < 24.0
    assert row["disk_alert"] is True


def test_observe_does_not_alert_on_growing_disk() -> None:
    watchdog = HostTrendWatchdog()
    for i in range(4):
        watchdog.observe(
            {"desk-1": {"disk_free_bytes": 100_000_000_000 + i * 50_000_000_000}},
            now=_NOW + timedelta(hours=i),
        )
    rows = watchdog.observe(
        {"desk-1": {"disk_free_bytes": 300_000_000_000}},
        now=_NOW + timedelta(hours=4),
    )
    assert rows[0]["disk_alert"] is False
    assert rows[0]["hours_to_full"] is None


def test_observe_flags_anomalous_metric_after_enough_samples() -> None:
    watchdog = HostTrendWatchdog()
    # Five stable in-flight-run samples, then one wild outlier.
    for i in range(5):
        watchdog.observe({"desk-1": {"disk_free_bytes": 100.0, "in_flight": 5}}, now=_NOW + timedelta(minutes=i))
    rows = watchdog.observe({"desk-1": {"disk_free_bytes": 100.0, "in_flight": 500}}, now=_NOW + timedelta(minutes=5))
    assert rows[0]["anomaly"] is True


def test_observe_keeps_hosts_independent() -> None:
    watchdog = HostTrendWatchdog()
    rows = watchdog.observe(
        {
            "desk-1": {"disk_free_bytes": 1.0},
            "desk-2": {"disk_free_bytes": 2.0},
        },
        now=_NOW,
    )
    hosts = {row["host"] for row in rows}
    assert hosts == {"desk-1", "desk-2"}


def test_observe_ignores_non_numeric_metric_values() -> None:
    watchdog = HostTrendWatchdog()
    rows = watchdog.observe({"desk-1": {"disk_free_bytes": "not-a-number", "online": True}}, now=_NOW)
    # "online" is a bool metric we don't track; the row still exists with disk_free_bytes absent.
    assert rows[0]["host"] == "desk-1"
    assert rows[0]["disk_free_bytes"] is None


def test_render_watchdog_lines_includes_help_and_type() -> None:
    watchdog = HostTrendWatchdog()
    rows = watchdog.observe({"desk-1": {"disk_free_bytes": 100.0}}, now=_NOW)
    lines = render_watchdog_lines(rows)
    text = "\n".join(lines)
    assert "# HELP rd_host_metric_anomaly" in text
    assert "# TYPE rd_host_metric_anomaly gauge" in text
    assert 'rd_host_metric_anomaly{host="desk-1"} 0' in text
    assert "# HELP rd_host_disk_fill_alert" in text
    assert 'rd_host_disk_fill_alert{host="desk-1"} 0' in text


def test_render_watchdog_lines_emits_hours_to_full_only_when_projected() -> None:
    watchdog = HostTrendWatchdog()
    for i in range(4):
        watchdog.observe(
            {"desk-1": {"disk_free_bytes": 1_000_000_000_000 - i * 400_000_000_000}},
            now=_NOW + timedelta(hours=i),
        )
    all_rows = watchdog.observe(
        {
            "desk-1": {"disk_free_bytes": 1_000_000_000_000 - 4 * 400_000_000_000},
            "desk-2": {"disk_free_bytes": 1.0},
        },
        now=_NOW + timedelta(hours=4),
    )
    lines = render_watchdog_lines(all_rows)
    text = "\n".join(lines)
    assert 'rd_host_disk_fill_hours_to_full{host="desk-1"}' in text
    # desk-2 has no shrinking trend yet, so it must not get a bogus hours-to-full line.
    assert 'rd_host_disk_fill_hours_to_full{host="desk-2"}' not in text


def test_render_watchdog_lines_handles_empty_rows() -> None:
    assert render_watchdog_lines([]) == []
