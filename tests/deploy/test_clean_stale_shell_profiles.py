# test_clean_stale_shell_profiles.py
"""Behavioral tests for deploy/clean-stale-shell-profiles.sh (Runner_Dashboard#1159)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SCRIPT = DEPLOY / "clean-stale-shell-profiles.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)


def test_clean_stale_profiles_removes_missing_cargo_env(tmp_path: Path) -> None:
    """Verify deleted cargo/env lines are pruned, while live ones and other profile contents are preserved."""
    valid_dir = tmp_path / "live_cargo"
    valid_dir.mkdir()
    valid_env = valid_dir / "env"
    valid_env.write_text("#live env\n", encoding="utf-8")

    missing_env = tmp_path / "deleted_cargo" / "env"

    profile = tmp_path / ".profile"
    profile_content = (
        "# User profile\n"
        "export FATHER=1  \n"
        f"source {as_bash_path(missing_env)}\n"
        f". '{as_bash_path(valid_env)}'\n"
        "source /home/dieterolson/actions-runners/runner-2/_work/Tools_Private/_ci/tools-private-cargo/env\n"
        "alias lll='ls -la'\n"
    )
    profile.write_text(profile_content, encoding="utf-8")

    assert BASH is not None
    args = [
        BASH,
        as_bash_path(SCRIPT),
        "--target-file",
        as_bash_path(profile),
    ]
    res = subprocess.run(args, capture_output=True, text=True)
    assert res.returncode == 0, f"script failed: {res.stderr}"

    cleaned = profile.read_text(encoding="utf-8")
    assert "# User profile" in cleaned
    assert "export FATHER=1" in cleaned
    assert "alias lll='ls -la'" in cleaned
    assert as_bash_path(valid_env) in cleaned
    assert "/Tools_Private/" not in cleaned
    assert as_bash_path(missing_env) not in cleaned

    backups = list(tmp_path.glob(".profile.bak.*"))
    assert len(backups) == 1


def test_clean_stale_profiles_dry_run(tmp_path: Path) -> None:
    profile = tmp_path / ".profile"
    content = ". /nonexistent/cargo/env\n"
    profile.write_text(content, encoding="utf-8")

    assert BASH is not None
    args = [
        BASH,
        as_bash_path(SCRIPT),
        "--dry-run",
        "--target-file",
        as_bash_path(profile),
    ]
    res = subprocess.run(args, capture_output=True, text=True)
    assert res.returncode == 0
    assert profile.read_text(encoding="utf-8") == content
    assert not list(tmp_path.glob(".profile.bak.*"))
