"""Tests for the barb-worker panel relay timer scripts (#3711)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "scripts" / "barb-worker"
PASS = SCRIPT_DIR / "panel-relay-pass.sh"
INSTALL = SCRIPT_DIR / "install-panel-relay.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)


def _bash(script: Path, *args: str, home: Path) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "RUNNER_DASHBOARD_ROOT": str(ROOT)}
    assert BASH is not None
    argv = [BASH, as_bash_path(script), *args]
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)


def test_scripts_exist_and_are_executable() -> None:
    for path in (PASS, INSTALL):
        assert path.is_file() and path.stat().st_mode & 0o111, path


def test_pass_refuses_to_run_without_a_bot_token(tmp_path: Path) -> None:
    result = _bash(PASS, "--dry-run", home=tmp_path)
    assert result.returncode == 3 and "refusing personal auth fallback" in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="checks the non-root refusal")
def test_installer_needs_root(tmp_path: Path) -> None:
    result = _bash(INSTALL, home=tmp_path)
    assert result.returncode == 2 and "run as root" in result.stderr


def test_installer_dry_runs_before_enabling_and_keeps_the_cap() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    assert text.index("--dry-run") < text.index("BARB_PANEL_RELAY_ENABLED=1")
    assert 'CAP="${BARB_PANEL_RELAY_DAILY_CAP:-3}"' in text
    assert "--disable" in text and "FLEET_API_TOKEN" not in text and "GH_TOKEN" not in text
