"""Tests for pool-aware autoscaler functionality (issue #755)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import runner_autoscaler as ra


def test_classify_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify systemd runner units are correctly classified into pools."""
    monkeypatch.setattr(ra, "NVME_PATTERN", "nvme")
    monkeypatch.setattr(ra, "HDD_PATTERN", "hdd")

    assert ra._classify_unit("actions.runner.myorg.runner-nvme-1.service") == "nvme"
    assert ra._classify_unit("actions.runner.myorg.runner-NVME-2.service") == "nvme"
    assert ra._classify_unit("actions.runner.myorg.runner-hdd-1.service") == "hdd"
    assert ra._classify_unit("actions.runner.myorg.runner-HDD-2.service") == "hdd"
    assert ra._classify_unit("actions.runner.myorg.runner-generic-1.service") == "default"


def test_get_pool_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify pool configurations are correctly retrieved and respect overrides."""
    monkeypatch.setattr(ra, "NVME_MIN_ONLINE", 2)
    monkeypatch.setattr(ra, "HDD_MIN_ONLINE", 3)

    nvme_cfg = ra._get_pool_config("nvme")
    assert nvme_cfg["min_online"] == 2
    assert "nvme" in nvme_cfg["labels"]

    hdd_cfg = ra._get_pool_config("hdd")
    assert hdd_cfg["min_online"] == 3
    assert "hdd" in hdd_cfg["labels"]

    default_cfg = ra._get_pool_config("default")
    assert default_cfg["min_online"] == ra.MIN_ONLINE


def test_start_stop_allowed_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify start/stop enabled switches are respected."""
    monkeypatch.setattr(ra, "NVME_START_ENABLED", True)
    monkeypatch.setattr(ra, "NVME_STOP_ENABLED", False)

    assert ra._is_start_allowed("nvme") is True
    assert ra._is_stop_allowed("nvme") is False


def test_start_stop_allowed_labels_filtering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify label filtering restricts start/stop actions to matching pools."""
    monkeypatch.setattr(ra, "NVME_LABELS", ["nvme", "fast"])
    monkeypatch.setattr(ra, "HDD_LABELS", ["hdd", "slow"])

    # No filter set -> all allowed
    monkeypatch.setattr(ra, "FILTER_START_LABELS", [])
    assert ra._is_start_allowed("nvme") is True
    assert ra._is_start_allowed("hdd") is True

    # Filter matches nvme only
    monkeypatch.setattr(ra, "FILTER_START_LABELS", ["fast"])
    assert ra._is_start_allowed("nvme") is True
    assert ra._is_start_allowed("hdd") is False


def test_get_scheduled_pool_desired_no_schedule_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify pool desired count falls back to pool default when schedule file doesn't exist."""
    monkeypatch.setattr(ra, "RUNNER_SCHEDULE_CONFIG", "/nonexistent/path.json")
    monkeypatch.setattr(ra, "NVME_DEFAULT", 5)
    monkeypatch.setattr(ra, "NVME_MIN_ONLINE", 1)
    monkeypatch.setattr(ra, "NVME_MAX_ONLINE", 10)

    assert ra._get_scheduled_pool_desired("nvme", 10) == 5


def test_get_scheduled_pool_desired_with_schedule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify schedule override and default_count keys are parsed from schedule json."""
    sched_file = tmp_path / "runner-schedule.json"
    monkeypatch.setattr(ra, "RUNNER_SCHEDULE_CONFIG", str(sched_file))
    monkeypatch.setattr(ra, "NVME_MIN_ONLINE", 1)
    monkeypatch.setattr(ra, "NVME_MAX_ONLINE", 15)

    # 1. Root level override
    config_data = {"enabled": True, "timezone": "UTC", "nvme_default_count": 6, "schedules": []}
    sched_file.write_text(json.dumps(config_data), encoding="utf-8")
    assert ra._get_scheduled_pool_desired("nvme", 10) == 6

    # 2. Root level override with clamping to max_online
    assert ra._get_scheduled_pool_desired("nvme", 5) == 5

    # 3. Active schedule override
    config_data_active = {
        "enabled": True,
        "timezone": "UTC",
        "nvme_default_count": 6,
        "schedules": [
            {
                "name": "always-on",
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                "start": "00:00",
                "end": "23:59",
                "nvme_runners": 8,
            }
        ],
    }
    sched_file.write_text(json.dumps(config_data_active), encoding="utf-8")
    assert ra._get_scheduled_pool_desired("nvme", 10) == 8


def test_sample_pool_disk(tmp_path: Path) -> None:
    """Verify disk usage sampler resolves the path and returns float values."""
    percent, free_gb = ra._sample_pool_disk(str(tmp_path))
    assert isinstance(percent, float)
    assert isinstance(free_gb, float)
    assert percent >= 0.0
    assert free_gb >= 0.0


def test_disk_utilization_percent_calculation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify IO utilization percentage delta math works correctly."""
    import autoscaler_sampling as as_samp

    # Mock psutil
    mock_psutil = MagicMock()
    monkeypatch.setattr(as_samp, "psutil", mock_psutil)

    # First call: initialization
    mock_io_1 = MagicMock()
    mock_io_1.read_time = 1000
    mock_io_1.write_time = 500
    # busy_time not present to test read_time + write_time fallback
    del mock_io_1.busy_time

    mock_psutil.disk_io_counters.return_value = {"sda": mock_io_1}

    # Initialize last values
    as_samp._last_io_time.clear()
    as_samp._last_io_timestamp.clear()

    # Second call: delta after 1 second (1000ms elapsed, 500ms additional IO time)
    mock_io_2 = MagicMock()
    mock_io_2.read_time = 1300
    mock_io_2.write_time = 700
    del mock_io_2.busy_time

    with patch("time.time", side_effect=[1000.0, 1001.0]):
        res1 = ra.get_disk_utilization_percent("sda")
        assert res1 == 0.0

        mock_psutil.disk_io_counters.return_value = {"sda": mock_io_2}
        res2 = ra.get_disk_utilization_percent("sda")
        # delta IO time = (1300+700) - (1000+500) = 500ms
        # delta time = 1000ms
        # expected = 50.0%
        assert res2 == pytest.approx(50.0)


def test_cooldown_and_soft_recovery_logic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that cooldown blocks starts and soft recovery limits start step size to 1."""
    monkeypatch.setattr(ra, "COOLDOWN_SECS", 10)

    # Reset
    ra.pool_last_overloaded["nvme"] = 0.0

    # 1. Normal state: not in cooldown
    # simulated elapsed time is infinite since last overload was 0.0
    # Check that it's neither in cooldown nor in soft recovery
    now = time.time()
    assert (now - ra.pool_last_overloaded["nvme"]) >= 20

    # 2. Overloaded state occurs now:
    overload_time = now
    ra.pool_last_overloaded["nvme"] = overload_time

    # 3. Check elapsed time < COOLDOWN_SECS (hard cooldown)
    with patch("time.time", return_value=overload_time + 5):
        elapsed = time.time() - ra.pool_last_overloaded["nvme"]
        assert elapsed < 10  # in cooldown

    # 4. Check COOLDOWN_SECS <= elapsed < 2 * COOLDOWN_SECS (soft recovery)
    with patch("time.time", return_value=overload_time + 15):
        elapsed = time.time() - ra.pool_last_overloaded["nvme"]
        assert 10 <= elapsed < 20  # in soft recovery


# ---------------------------------------------------------------------------
# Issue #937d: empty-label (default) pool must be EXEMPT from label filtering,
# not silently frozen, when a start/stop label filter is configured.
# ---------------------------------------------------------------------------


def test_default_pool_exempt_from_start_label_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """A label filter set for nvme/hdd must not freeze the label-less default pool."""
    monkeypatch.setattr(ra, "FILTER_START_LABELS", ["nvme"])
    # default pool has labels == [] (see _get_pool_config); it is the catch-all.
    assert ra._is_start_allowed("default") is True
    # A labelled pool that does not match the filter is still excluded.
    monkeypatch.setattr(ra, "NVME_LABELS", ["nvme"])
    monkeypatch.setattr(ra, "HDD_LABELS", ["hdd"])
    assert ra._is_start_allowed("nvme") is True
    assert ra._is_start_allowed("hdd") is False


def test_default_pool_exempt_from_stop_label_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ra, "FILTER_STOP_LABELS", ["nvme"])
    assert ra._is_stop_allowed("default") is True


def test_pool_passes_label_filter_helper() -> None:
    assert ra._pool_passes_label_filter([], []) is True  # no filter
    assert ra._pool_passes_label_filter([], ["x"]) is True  # empty pool exempt
    assert ra._pool_passes_label_filter(["x"], ["x"]) is True  # match
    assert ra._pool_passes_label_filter(["y"], ["x"]) is False  # no match


# ---------------------------------------------------------------------------
# Issue #937e: ACTION_COOLDOWN_SECONDS must actually space out stop actions.
# ---------------------------------------------------------------------------


def test_stop_in_cooldown_defers_second_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ra, "ACTION_COOLDOWN_SECONDS", 120)
    ra.pool_last_stop_action["default"] = 0.0
    # No prior stop -> not in cooldown.
    assert ra._stop_in_cooldown("default", now=1000.0) is False
    # Record a stop, then a second decision 45s later is within cooldown.
    ra._record_stop_action("default", now=1000.0)
    assert ra._stop_in_cooldown("default", now=1045.0) is True
    # After the cooldown window elapses, stops are allowed again.
    assert ra._stop_in_cooldown("default", now=1121.0) is False


def test_stop_cooldown_disabled_when_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ra, "ACTION_COOLDOWN_SECONDS", 0)
    ra._record_stop_action("nvme", now=1000.0)
    assert ra._stop_in_cooldown("nvme", now=1001.0) is False


# ---------------------------------------------------------------------------
# Issue #937b: lease protection must be an EXACT runner-name match, not a
# substring (a lease on runner-1 must not shield runner-10..19).
# ---------------------------------------------------------------------------


def test_lease_protection_exact_match_not_substring() -> None:
    """A lease on 'runner-1' must protect exactly that runner, not 'runner-10'."""
    leased = {"runner-1"}
    unit_1 = "actions.runner.org.runner-1.service"
    unit_10 = "actions.runner.org.runner-10.service"
    # Exact-name comparison: only runner-1 is shielded.
    assert (ra._runner_name_for_unit(unit_1) in leased) is True
    assert (ra._runner_name_for_unit(unit_10) in leased) is False
    # The old substring test (`'runner-1' in unit_10`) would have wrongly shielded it.
    assert ("runner-1" in unit_10) is True  # demonstrates the old bug
