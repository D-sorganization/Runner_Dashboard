"""Contract tests for host-metrics-probe systemd wiring (#4309)."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"


def test_timer_samples_every_thirty_seconds() -> None:
    timer = (DEPLOY / "systemd" / "runner-dashboard-host-metrics-probe.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=30s" in timer
    assert "runner-dashboard-host-metrics-probe.service" in timer


def test_service_invokes_deployed_probe_script() -> None:
    unit = (DEPLOY / "systemd" / "runner-dashboard-host-metrics-probe.service").read_text(encoding="utf-8")
    assert "host-metrics-probe.py" in unit
    assert "RUNNER_DASHBOARD_STATE_DIR=@STATE_DIR@" in unit


def test_install_script_enables_timer_in_update_deployed_and_installer() -> None:
    update = (DEPLOY / "update-deployed.sh").read_text(encoding="utf-8")
    assert "install-host-metrics-probe.sh" in update
    assert "install_host_metrics_probe_timer" in update
    install = (DEPLOY / "install-host-metrics-probe.sh").read_text(encoding="utf-8")
    assert "runner-dashboard-host-metrics-probe.timer" in install
    assert "systemctl --user enable --now runner-dashboard-host-metrics-probe.timer" in install
    assert "systemctl enable --now runner-dashboard-host-metrics-probe.timer" in install
