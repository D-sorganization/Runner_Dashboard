"""Deploy loopback bind guard (#3744)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "deploy" / "loopback_bind_guard.py"


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / "env"
    path.write_text("DASHBOARD_LOOPBACK_AUTH=1\n", encoding="utf-8")
    return path


def test_diagnose_missing_host(env_file: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(GUARD), "diagnose", "--env-file", str(env_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["reason"] == "loopback_auth_missing_host"


def test_ensure_env_sets_loopback_host(env_file: Path) -> None:
    subprocess.run(
        [sys.executable, str(GUARD), "ensure-env", "--env-file", str(env_file)],
        check=True,
    )
    text = env_file.read_text(encoding="utf-8")
    assert "DASHBOARD_HOST=127.0.0.1" in text
    proc = subprocess.run(
        [sys.executable, str(GUARD), "diagnose", "--env-file", str(env_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(proc.stdout) is None


def test_diagnose_rejects_all_interfaces_host(tmp_path: Path) -> None:
    path = tmp_path / "env"
    path.write_text("DASHBOARD_LOOPBACK_AUTH=1\nDASHBOARD_HOST=0.0.0.0\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(GUARD), "diagnose", "--env-file", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(proc.stdout)["reason"] == "loopback_auth_all_interfaces"


def test_install_user_dropin(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env_path = home / ".config/runner-dashboard/env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DASHBOARD_LOOPBACK_AUTH=1\nDASHBOARD_HOST=0.0.0.0\n", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "repair",
            "--env-file",
            str(env_path),
            "--scope",
            "user",
            "--home",
            str(home),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert "env_set_dashboard_host_loopback" in payload["actions"]
    dropin = home / ".config/systemd/user/runner-dashboard.service.d/99-loopback-host.conf"
    assert dropin.is_file()
    assert "DASHBOARD_HOST=127.0.0.1" in dropin.read_text(encoding="utf-8")
