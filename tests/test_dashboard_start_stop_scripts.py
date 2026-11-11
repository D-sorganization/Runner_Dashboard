"""Regression tests for start/stop dashboard shell scripts (#4268, H-02/H-03)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
START = REPO_ROOT / "start-dashboard.sh"
STOP = REPO_ROOT / "stop-dashboard.sh"
PROCESS_HELPERS = REPO_ROOT / "scripts" / "dashboard-process.sh"
BASH = os.environ.get("TEST_BASH") or "/bin/bash"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_scripts_exist() -> None:
    assert START.is_file()
    assert STOP.is_file()
    assert PROCESS_HELPERS.is_file()


def test_stop_uses_pid_file_not_pgrep() -> None:
    text = _read(STOP)
    assert "pgrep" not in text
    assert "PID_FILE" in text or ".runner-dashboard.pid" in text
    assert "dashboard-process.sh" in text
    assert "dashboard_pid_is_dashboard" in text


def test_start_bg_writes_pid_file_and_waits_for_health() -> None:
    text = _read(START)
    assert "nohup bash -c" not in text
    assert "PID_FILE" in text
    assert "wait_for_dashboard_health" in text
    assert ">>" in text and "LOG_FILE" in text


def test_start_sources_dashboard_process_helpers() -> None:
    assert 'source "${SCRIPT_DIR}/scripts/dashboard-process.sh"' in _read(START)


@pytest.mark.skipif(not Path("/proc").exists(), reason="/proc required for cmdline identity")
def test_dashboard_pid_is_dashboard_rejects_unrelated_process(tmp_path: Path) -> None:
    proc = subprocess.Popen(["sleep", "120"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        repo = tmp_path / "repo"
        (repo / "backend").mkdir(parents=True)
        script = (
            f'source "{PROCESS_HELPERS}"\n'
            f'if dashboard_pid_is_dashboard {proc.pid} "{repo}"; then exit 0; else exit 1; fi\n'
        )
        res = subprocess.run([BASH, "-c", script], capture_output=True, text=True, check=False)
        assert res.returncode == 1
    finally:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.skipif(not Path("/proc").exists(), reason="/proc required for cmdline identity")
def test_dashboard_pid_is_dashboard_accepts_server_py_argv() -> None:
    """Linux exec -a sets argv0 so identity matches without starting the full app."""
    server_marker = f"{REPO_ROOT}/backend/server.py"
    proc = subprocess.Popen(
        [BASH, "-c", f'exec -a "{server_marker}" sleep 120'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        script = (
            f'source "{PROCESS_HELPERS}"\n'
            f'if dashboard_pid_is_dashboard {proc.pid} "{REPO_ROOT}"; then exit 0; else exit 1; fi\n'
        )
        res = subprocess.run([BASH, "-c", script], capture_output=True, text=True, check=False)
        assert res.returncode == 0
    finally:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.skipif(not Path(BASH).is_file(), reason="bash required")
def test_stop_refuses_wrong_pid_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / "dashboard.pid"
    sleep_proc = subprocess.Popen(["sleep", "300"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid_file.write_text(str(sleep_proc.pid), encoding="utf-8")
    env = {**os.environ, "RUNNER_DASHBOARD_PID_FILE": str(pid_file)}
    try:
        res = subprocess.run(
            [BASH, str(STOP)],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert sleep_proc.poll() is None, "stop must not kill a non-dashboard pid"
        assert res.returncode != 0 or "identity" in res.stdout.lower() or "stale" in res.stdout.lower()
    finally:
        sleep_proc.kill()
        sleep_proc.wait(timeout=5)
