"""Contract tests for auto-deploy install artifacts."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"


def test_install_script_detects_user_scope_without_sudo_system_install() -> None:
    text = (DEPLOY / "install-node-auto-deploy.sh").read_text(encoding="utf-8")
    assert "systemctl --user enable" in text
    assert 'SCOPE="$(detect_scope)"' in text
    assert "sudo systemctl enable" in text


def test_systemd_timer_runs_every_ten_minutes() -> None:
    timer = (DEPLOY / "systemd" / "runner-dashboard-auto-deploy.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=10min" in timer


def test_service_invokes_deployed_wrapper() -> None:
    unit = (DEPLOY / "systemd" / "runner-dashboard-auto-deploy.service").read_text(encoding="utf-8")
    assert "node-auto-deploy.sh" in unit
    assert "RUNNER_DASHBOARD_AUTO_DEPLOY=1" in unit
    assert "User=@DASHBOARD_USER@" in unit
    assert "Environment=HOME=@DASHBOARD_HOME@" in unit
    assert "RUNNER_DASHBOARD_ENV_FILE" in unit


def test_install_renders_user_unit_without_system_user_line() -> None:
    text = (DEPLOY / "install-node-auto-deploy.sh").read_text(encoding="utf-8")
    assert "sed '/^User=/d'" in text
    assert "auto-deploy-env.sh" in text
