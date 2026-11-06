"""Fake-worker tests for backlog-push-lane.sh (#4104). Refs #4104."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from bash_host import find_bash

_ROOT = Path(__file__).resolve().parents[2]
BASH = find_bash()


def _lane_fixture(tmp_path: Path, exit_code: int) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "rd"
    worker_dir = root / "scripts/barb-worker"
    worker_dir.mkdir(parents=True)
    shutil.copy(_ROOT / "scripts/barb-worker/with-fleet-env.sh", worker_dir / "with-fleet-env.sh")
    shutil.copy(_ROOT / "scripts/barb-worker/backlog-push-lane.sh", worker_dir / "backlog-push-lane.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    fake_claude.chmod(0o755)
    fake_gh = bin_dir / "gh"
    fake_gh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_gh.chmod(0o755)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt = log_dir / "sess.prompt"
    prompt.write_text("hi", encoding="utf-8")
    log_path = log_dir / "sess.log"
    pidfile = log_dir / "sess.pid"
    return worker_dir / "backlog-push-lane.sh", log_path, prompt, pidfile


@pytest.mark.skipif(BASH is None, reason="POSIX bash required")
def test_lane_records_success_exit_and_exits_zero(tmp_path: Path) -> None:
    lane_sh, log_path, prompt, pidfile = _lane_fixture(tmp_path, 0)
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir(exist_ok=True)
    env["PATH"] = f"{tmp_path / 'bin'}:{env['PATH']}"
    env["RUNNER_DASHBOARD_ROOT"] = str(tmp_path / "rd")
    env["BARB_BACKLOG_PUSH_GH_REFRESH_S"] = "1"
    proc = subprocess.run(
        [BASH, str(lane_sh), "sess", str(log_path), str(prompt), str(pidfile)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    exitfile = pidfile.with_suffix(".exit")
    assert proc.returncode == 0
    assert exitfile.read_text(encoding="utf-8").strip() == "0"


@pytest.mark.skipif(BASH is None, reason="POSIX bash required")
def test_lane_records_failure_exit_37(tmp_path: Path) -> None:
    lane_sh, log_path, prompt, pidfile = _lane_fixture(tmp_path, 37)
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir(exist_ok=True)
    env["PATH"] = f"{tmp_path / 'bin'}:{env['PATH']}"
    env["RUNNER_DASHBOARD_ROOT"] = str(tmp_path / "rd")
    env["BARB_BACKLOG_PUSH_GH_REFRESH_S"] = "1"
    proc = subprocess.run(
        [BASH, str(lane_sh), "sess", str(log_path), str(prompt), str(pidfile)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    exitfile = pidfile.with_suffix(".exit")
    assert proc.returncode == 37
    assert exitfile.read_text(encoding="utf-8").strip() == "37"
