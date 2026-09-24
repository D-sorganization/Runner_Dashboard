# test_staff_node_acceptance.py
"""Tests for deploy/staff-node-acceptance.sh (Runner_Dashboard#1273)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SCRIPT = DEPLOY / "staff-node-acceptance.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run([BASH, as_bash_path(SCRIPT), *args], capture_output=True, text=True)


def test_help_option() -> None:
    res = _run("--help")
    assert res.returncode == 0, res.stderr
    assert "Usage: staff-node-acceptance.sh" in res.stdout
    assert "--role" in res.stdout
    assert "--env-file" in res.stdout
    assert "--dropin-file" in res.stdout
    assert "--skip-service" in res.stdout


def test_invalid_role_fails() -> None:
    res = _run("--role", "invalid_role")
    assert res.returncode == 2
    assert "Invalid role" in res.stderr


def test_missing_env_file_fails(tmp_path: Path) -> None:
    fake_env = tmp_path / "nonexistent_env"
    fake_dropin = tmp_path / "nonexistent_dropin.conf"
    res = _run(
        "--role",
        "worker",
        "--env-file",
        as_bash_path(fake_env),
        "--dropin-file",
        as_bash_path(fake_dropin),
        "--skip-service",
        "--skip-network",
    )
    # The script should report failure since files don't exist and health endpoint is not up
    assert res.returncode == 1
    assert "Service env file missing" in res.stderr or "Service env file missing" in res.stdout
