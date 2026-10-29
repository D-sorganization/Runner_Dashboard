"""I/O-pressure admission and recovery through the real poll loop (#3026, #3553)."""

import logging
import sys
from unittest.mock import Mock

import autoscaler_sampling as samp
import pytest
import runner_autoscaler as ra
import system_utils


class PollFinished(BaseException):
    """Stop the otherwise infinite poll without entering its error recovery."""


@pytest.mark.parametrize(
    ("pressures", "expected_starts"),
    [([35, 35, 35], []), ([80, 80, 80], []), ([80, 80, 80, 20, 10, 9], [9]), ([None, None, None], [None])],
)
def test_io_pressure_used_in_poll(monkeypatch, caplog, pressures, expected_starts):
    caplog.set_level(logging.WARNING, logger="runner-autoscaler")
    current = [None]
    remaining = iter(pressures)

    def pressure():
        try:
            current[0] = next(remaining)
        except StopIteration:
            raise PollFinished from None
        return None if current[0] is None else {"full": {"avg10": current[0]}}

    reader = Mock(side_effect=pressure)
    # Patch both modules so the test works whether sampling imports the reader
    # locally or re-exports it, without assuming either implementation choice.
    monkeypatch.setattr(system_utils, "get_io_pressure_snapshot", reader)
    monkeypatch.setattr(ra, "get_io_pressure_snapshot", reader, raising=False)
    monkeypatch.setattr(ra, "_sample", lambda: (0, 0, 0, 0, 1000))
    monkeypatch.setattr(ra, "_sample_pool_disk", lambda path: (0, 1000))
    monkeypatch.setattr(ra, "get_disk_utilization_percent", lambda device: 0)
    monkeypatch.setattr(ra, "_get_pool_config", lambda pool: {"path": "/unused", "min_online": 0})
    monkeypatch.setattr(ra, "_list_runner_units", lambda: ["fixture-runner"])
    monkeypatch.setattr(ra, "_classify_unit", lambda unit: "default")
    monkeypatch.setattr(ra, "_leased_runners", lambda: set())
    monkeypatch.setattr(ra, "_lease_scale_down_guard", lambda: samp.LeaseScaleDownGuard())
    monkeypatch.setattr(ra, "_unit_is_active", lambda unit: False)
    monkeypatch.setattr(ra, "_runner_is_busy", lambda unit: False)
    monkeypatch.setattr(ra, "_get_scheduled_pool_desired", lambda pool, count: 1)
    monkeypatch.setattr(ra, "_is_start_allowed", lambda pool: True)
    monkeypatch.setattr(ra, "process_disk_full_cordon_tick", lambda *args, **kwargs: False)
    monkeypatch.setattr(ra, "_notify_systemd", lambda state: None)
    monkeypatch.setattr(ra, "_send_watchdog", lambda: None)
    monkeypatch.setattr(ra, "SUSTAIN_SECS", 0)
    monkeypatch.setattr(ra, "POLL_SECONDS", 1)
    monkeypatch.setattr(ra, "CPU_HIGH", 85.0)
    monkeypatch.setattr(ra, "CPU_LOW", 40.0)
    monkeypatch.setattr(ra, "MEM_HIGH", 85.0)
    monkeypatch.setattr(ra, "LOAD_PER_CORE", 2.5)
    monkeypatch.setattr(ra, "DISK_HIGH", 92.0)
    monkeypatch.setattr(ra, "DISK_MIN_FREE_GB", 25.0)
    monkeypatch.setattr(ra, "IO_PRESSURE_FULL_HIGH", 35)
    monkeypatch.setattr(ra, "IO_PRESSURE_FULL_LOW", 10)
    monkeypatch.setattr(ra, "MIN_ONLINE", 0)
    monkeypatch.setattr(ra, "MAX_STEP", 1)
    monkeypatch.setattr(ra, "COOLDOWN_SECS", 0)
    monkeypatch.setattr(ra, "ACTION_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(ra, "pool_last_overloaded", dict.fromkeys(["default", "nvme", "hdd"], 0.0))
    starts = []
    monkeypatch.setattr(ra, "_start_unit", lambda unit: starts.append(current[0]))
    monkeypatch.setattr(ra, "_stop_unit", Mock(side_effect=AssertionError("unexpected stop")))
    ticks = [0]

    def sleep(seconds):
        ticks[0] += 1
        # On unchanged main the PSI reader is never called; still stop safely.
        if ticks[0] >= len(pressures):
            raise PollFinished

    monkeypatch.setattr(ra.time, "sleep", sleep)
    with pytest.raises(PollFinished):
        ra._run_poll_loop()
    assert reader.call_count == len(pressures)
    assert starts == expected_starts
    if pressures[0] is None:
        assert "PSI I/O pressure unavailable" in caplog.text


def test_leases_path_falls_back_when_runner_lease_unavailable(monkeypatch, tmp_path):
    """_leases_path and _lease_scale_down_guard work even without runner_lease/FastAPI (#3553)."""
    monkeypatch.setenv("RUNNER_DASHBOARD_CONFIG_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "runner_lease", None)
    assert samp._leases_path() == tmp_path / "leases.yml"
    guard = samp._lease_scale_down_guard()
    assert guard.protected_runner_names == frozenset()
    assert guard.block_scale_down is False
    assert ra._extract_io_full_avg10({"full_avg10": 42.5}) == 42.5
    assert ra._extract_io_full_avg10({"full": {"avg10": 17.25}}) == 17.25
