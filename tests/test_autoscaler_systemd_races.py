"""RD-21/22: slow state probes and a job picked up before stop (#3027)."""

import subprocess
from collections.abc import Callable
from unittest.mock import Mock

import autoscaler_busy as busy
import autoscaler_systemd as sd
import pytest

pytestmark = pytest.mark.unit
UNIT = "actions.runner.example.worker-1.service"


@pytest.mark.parametrize("probe", [sd._unit_is_active, sd._unit_state])
@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("systemctl", 5), OSError("unavailable")])
def test_unit_state_timeout_is_conservative(
    monkeypatch: pytest.MonkeyPatch, probe: Callable, failure: Exception
) -> None:
    monkeypatch.setattr(sd.subprocess, "run", Mock(side_effect=failure))
    expected = True if probe is sd._unit_is_active else ("unknown", "unknown")
    assert probe(UNIT) == expected


def test_failed_unit_state_query_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sd.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 1, stdout="")))
    assert sd._unit_state(UNIT) == ("unknown", "unknown")


def test_busy_fallback_treats_unknown_state_as_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(busy, "_runner_busy_via_lockfile", lambda unit: False)
    monkeypatch.setattr(busy, "_runner_busy_via_pickup_dir_public", lambda unit: False)
    monkeypatch.setattr(busy, "_runner_busy_via_worker_scan_public", lambda unit: False)
    monkeypatch.setattr(busy, "_psutil_dep", lambda: None)
    monkeypatch.setattr(busy, "_unit_state", lambda unit: ("unknown", "unknown"))
    monkeypatch.setattr(sd.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, stdout="0")))
    assert busy._runner_is_busy(UNIT) is True


def test_rd_22_job_picked_up_after_sample_prevents_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    # The tick saw idle. Before the stop decision reaches systemd, pickup occurs.
    probe = Mock(side_effect=[False, True])
    monkeypatch.setattr(busy, "_runner_is_busy", probe)
    assert busy._runner_is_busy(UNIT) is False
    monkeypatch.setattr(sd, "_dry_run_enabled", lambda: False)
    monkeypatch.setattr(sd, "_unit_has_safe_stop_contract", lambda unit: True)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stderr=""))
    monkeypatch.setattr(sd.subprocess, "run", run)
    cleanup = Mock()
    monkeypatch.setattr("runner_state_cleanup.cleanup_runner_state", cleanup)
    assert sd._stop_unit(UNIT) is False
    assert probe.call_count == 2
    run.assert_not_called()
    cleanup.assert_not_called()
