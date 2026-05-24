"""Tests for autoscaler Prometheus metrics (issue #710)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


def test_autoscaler_metrics_defined():
    import prometheus_metrics as pm

    assert hasattr(pm, "AUTOSCALER_SCALING_ACTIONS_TOTAL")
    assert hasattr(pm, "AUTOSCALER_BUSY_RUNNERS")
    assert hasattr(pm, "AUTOSCALER_ACTIVE_RUNNERS")
    assert hasattr(pm, "AUTOSCALER_LOAD_RATIO")
    assert hasattr(pm, "AUTOSCALER_BUSY_CHECK_DURATION")
    assert hasattr(pm, "AUTOSCALER_DECISION_LOOP_DURATION")
    assert hasattr(pm, "AUTOSCALER_SYSTEMD_ERRORS_TOTAL")


def test_record_autoscaler_action_valid():
    import prometheus_metrics as pm

    pm.record_autoscaler_action("start", "idle")
    pm.record_autoscaler_action("stop", "busy")
    pm.record_autoscaler_action("start", "busy")
    pm.record_autoscaler_action("stop", "idle")
    pm.record_autoscaler_action("start", "load")
    pm.record_autoscaler_action("stop", "manual")


def test_record_autoscaler_action_invalid_raises():
    import prometheus_metrics as pm

    with pytest.raises(AssertionError):
        pm.record_autoscaler_action("restart", "idle")
    with pytest.raises(AssertionError):
        pm.record_autoscaler_action("stop", "unknown_reason")


def test_record_autoscaler_systemd_error():
    import prometheus_metrics as pm

    pm.record_autoscaler_systemd_error("stop", "actions.runner.test.service")
    pm.record_autoscaler_systemd_error("start", "actions.runner.another.service")


def test_lease_reaper_metrics_defined():
    """A2 (#708): LEASE_REAPER_PRUNED_TOTAL and LEASE_ACTIVE_TOTAL must exist."""
    import prometheus_metrics as pm

    assert hasattr(pm, "LEASE_REAPER_PRUNED_TOTAL")
    assert hasattr(pm, "LEASE_ACTIVE_TOTAL")
