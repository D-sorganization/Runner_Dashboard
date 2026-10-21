"""Systemd unit rendering and HOME-safe entrypoint for node auto-deploy."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"
BASH = os.environ.get("BASH", "/bin/bash")


def _render_system_unit(
    tmp_path: Path,
    *,
    user: str,
    home: str,
    deploy: str,
    env_file: str,
    python_bin: str,
) -> str:
    template = (DEPLOY / "systemd" / "runner-dashboard-auto-deploy.service").read_text(encoding="utf-8")
    rendered = (
        template.replace("@DASHBOARD_USER@", user)
        .replace("@DASHBOARD_HOME@", home)
        .replace("@DEPLOY_DIR@", deploy)
        .replace("@ENV_FILE@", env_file)
        .replace("@PYTHON_BIN@", python_bin)
    )
    unit = tmp_path / "runner-dashboard-auto-deploy.service"
    unit.write_text(rendered, encoding="utf-8")
    return unit.read_text(encoding="utf-8")


def test_system_unit_sets_user_and_home(tmp_path: Path) -> None:
    text = _render_system_unit(
        tmp_path,
        user="dieterolson",
        home="/home/dieterolson",
        deploy="/home/dieterolson/actions-runners/dashboard",
        env_file="/home/dieterolson/.config/runner-dashboard/env",
        python_bin="/home/dieterolson/actions-runners/dashboard/.venv/bin/python",
    )
    assert "User=dieterolson" in text
    assert "Environment=HOME=/home/dieterolson" in text
    assert "Environment=RUNNER_DASHBOARD_ENV_FILE=/home/dieterolson/.config/runner-dashboard/env" in text
    assert "Environment=RUNNER_DASHBOARD_PYTHON=/home/dieterolson/actions-runners/dashboard/.venv/bin/python" in text


@pytest.mark.skipif(not Path(BASH).exists(), reason="bash required")
def test_entrypoint_runs_with_home_unset(tmp_path: Path) -> None:
    """Regression: system oneshot services often have no HOME (DeskComputer)."""
    deploy = tmp_path / "dashboard"
    deploy.mkdir()
    script = deploy / "deploy" / "node-auto-deploy.sh"
    script.parent.mkdir(parents=True)
    script.write_text((DEPLOY / "node-auto-deploy.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (deploy / "deploy" / "auto-deploy-env.sh").write_text(
        (DEPLOY / "auto-deploy-env.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (deploy / "deploy" / "resolve-auto-deploy-python.sh").write_text(
        (DEPLOY / "resolve-auto-deploy-python.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    venv_python = deploy / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(sys.executable)
    stub = deploy / "deploy" / "node-auto-deploy.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    os.chmod(script, 0o755)
    env = os.environ.copy()
    env.pop("HOME", None)
    env["RUNNER_DASHBOARD_DEPLOY_DIR"] = str(deploy)
    env["RUNNER_DASHBOARD_ENV_FILE"] = str(tmp_path / "missing.env")
    proc = subprocess.run(
        [BASH, str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
