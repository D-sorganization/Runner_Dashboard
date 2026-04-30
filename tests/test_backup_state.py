"""Tests for the backup_state step in scheduled-dashboard-maintenance.sh.

Issue #417: dry-run mode must list the paths that would be archived
without actually creating the tarball, and BACKUP_DIR must be overridable
via environment variable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPT = _ROOT / "deploy" / "scheduled-dashboard-maintenance.sh"


def test_script_exists() -> None:
    assert _SCRIPT.is_file(), f"missing {_SCRIPT}"


def test_script_passes_bash_syntax_check() -> None:
    result = subprocess.run(
        ["bash", "-n", str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_script_defines_backup_state_function() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "backup_state()" in text or "backup_state ()" in text, (
        "scheduled-dashboard-maintenance.sh must define a backup_state function"
    )


def test_script_uses_default_backup_dir() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "/var/backups/runner-dashboard" in text, "default BACKUP_DIR must be /var/backups/runner-dashboard"


def test_script_retains_thirty_backups() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    # Either +31 (skip first 30) or +$((BACKUP_RETENTION+1)) — accept the literal default.
    assert "+31" in text or "BACKUP_RETENTION" in text, "retention policy must keep the most recent 30 backups"


def test_script_calls_backup_state_in_main() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    # Backup must be invoked from main() so cron picks it up.
    main_section = text.split("main()", 1)[-1]
    assert "backup_state" in main_section, "main() must invoke backup_state so cron triggers it"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_dry_run_lists_paths_and_creates_no_tarball(tmp_path: Path) -> None:
    """Run the script with --dry-run and a tmp BACKUP_DIR, assert no tarball."""

    backup_dir = tmp_path / "backups"
    fake_repo = tmp_path / "repo"
    fake_dashboard = fake_repo / "runner-dashboard"
    (fake_repo / ".git").mkdir(parents=True)
    fake_dashboard.mkdir(parents=True)
    # Copy the maintenance script into the fake dashboard so its path discovery works.
    shutil.copytree(_ROOT / "deploy", fake_dashboard / "deploy")

    env = dict(os.environ)
    env["BACKUP_DIR"] = str(backup_dir)
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / ".config" / "runner-dashboard").mkdir(parents=True)

    script = fake_dashboard / "deploy" / "scheduled-dashboard-maintenance.sh"
    result = subprocess.run(
        ["bash", str(script), "--dry-run", "--backup-only"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(fake_dashboard),
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"dry-run failed: {combined}"
    assert "DRY-RUN" in combined or "dry-run" in combined.lower()
    # Lists what it would archive
    assert "config" in combined
    assert "runner-dashboard" in combined
    # Mentions the would-be tarball name
    assert "runner-dashboard-state-" in combined
    assert ".tar.gz" in combined
    # No actual tarball was created
    if backup_dir.exists():
        produced = list(backup_dir.glob("runner-dashboard-state-*.tar.gz"))
        assert produced == [], f"dry-run should not create tarballs, got {produced}"
