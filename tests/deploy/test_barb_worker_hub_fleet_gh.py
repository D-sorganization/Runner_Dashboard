"""Hub-minted gh/git installer for barb-worker VMs (#3657, bootstrap #3659)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "barb-worker" / "install-hub-fleet-gh.sh"


def test_install_hub_fleet_gh_hook_contract() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert INSTALLER.stat().st_mode & 0o111
    assert "barb_github_hub_token.py" in text
    assert "echo \"${TOKEN}" not in text and "printf '%s' \"${TOKEN}\"" not in text


def test_install_hub_fleet_gh_dry_run(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash required")
    rd = tmp_path / "Runner_Dashboard" / "scripts"
    rd.mkdir(parents=True)
    (rd / "barb_github_hub_token.py").write_text("# stub\n", encoding="utf-8")
    fake_gh = tmp_path / "gh"
    fake_gh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_gh.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    proc = subprocess.run(
        [bash, str(INSTALLER), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "HOME": str(home),
            "RUNNER_DASHBOARD_ROOT": str(rd.parent),
            "STAFF_REAL_GH": str(fake_gh),
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert "dry-run" in proc.stdout
