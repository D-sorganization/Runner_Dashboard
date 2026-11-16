"""Host health gauge (#4310)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from host_health import _active_schedule_window, build_host_health_gauge


def test_build_host_health_gauge_red_when_cpu_hot() -> None:
    system = {
        "cpu": {"percent": 95},
        "memory": {"percent": 40},
        "runner_capacity": {
            "max_runners": 6,
            "state": {"desired": 6, "online": 6, "busy": 2},
            "schedule": {"timezone": "UTC", "default_count": 2, "schedules": []},
        },
    }
    gauge = build_host_health_gauge("hub-a", system)
    assert gauge["level"] == "red"
    assert gauge["suggested_action"] is not None
    assert "lower target" in gauge["suggested_action"]


def test_overnight_schedule_window_active_after_midnight_on_start_day() -> None:
    capacity = {
        "schedule": {
            "timezone": "UTC",
            "default_count": 1,
            "schedules": [
                {
                    "name": "overnight",
                    "days": ["mon"],
                    "start": "22:00",
                    "end": "06:00",
                    "runners": 4,
                }
            ],
        },
        "state": {},
    }
    # Tuesday 03:00 UTC: still inside Monday night's window.
    window = _active_schedule_window(capacity, at=datetime(2026, 11, 10, 3, 0, tzinfo=UTC))
    assert window is not None
    assert window["name"] == "overnight"
    assert window["runners"] == 4


def test_build_host_health_gauge_amber_when_cache_degraded() -> None:
    system = {
        "cpu": {"percent": 5},
        "memory": {"percent": 10},
        "runner_capacity": {
            "max_runners": 6,
            "state": {"desired": 4, "online": 4, "busy": 0},
            "schedule": {"timezone": "UTC", "default_count": 4, "schedules": []},
        },
    }
    provider = MagicMock()
    provider.read_cached.return_value = {
        "status": "degraded",
        "source": "cache",
        "cpu": {"percent": None},
        "memory": {"percent": None},
    }
    with patch("host_health.get_host_metrics_provider", return_value=provider):
        gauge = build_host_health_gauge("node-b", system)
    assert gauge["level"] == "amber"
    assert gauge["metrics_source"] == "cache"
    assert gauge["cpu_percent"] is None
    assert gauge["memory_percent"] is None


def test_stub_metrics_provider_falls_back_to_system() -> None:
    system = {
        "cpu": {"percent": 10},
        "memory": {"percent": 20},
        "runner_capacity": {
            "max_runners": 6,
            "state": {"desired": 4, "online": 4, "busy": 0},
            "schedule": {"timezone": "UTC", "default_count": 4, "schedules": []},
        },
    }
    gauge = build_host_health_gauge("node-b", system)
    assert gauge["level"] == "green"
    assert gauge["metrics_source"] == "system"
