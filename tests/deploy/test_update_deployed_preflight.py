"""Tests for update-deployed.sh pre-flight validation (issue #713)."""

import os
import subprocess
from pathlib import Path

DEPLOY_DIR_ROOT = Path(__file__).resolve().parents[2]
UPDATE_SCRIPT = DEPLOY_DIR_ROOT / "deploy" / "update-deployed.sh"
ROLLBACK_SCRIPT = DEPLOY_DIR_ROOT / "deploy" / "rollback-deployed.sh"


def _is_executable_script(path: Path) -> bool:
    if os.name != "nt":
        return bool(path.stat().st_mode & 0o111)
    rel = path.relative_to(DEPLOY_DIR_ROOT).as_posix()
    result = subprocess.run(
        ["git", "ls-files", "--stage", rel],
        cwd=DEPLOY_DIR_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.startswith("100755 ")


def test_update_script_exists():
    assert UPDATE_SCRIPT.exists()


def test_rollback_script_exists():
    assert ROLLBACK_SCRIPT.exists()


def test_rollback_script_is_executable():
    assert _is_executable_script(ROLLBACK_SCRIPT)


def test_update_script_has_preflight_check():
    content = UPDATE_SCRIPT.read_text()
    assert "py_compile" in content or "pre-flight" in content.lower(), (
        "update-deployed.sh must have pre-flight syntax check"
    )


def test_update_script_has_rollback_trigger():
    content = UPDATE_SCRIPT.read_text()
    assert "rollback" in content.lower(), "update-deployed.sh must trigger rollback on failure"


def test_rollback_script_has_rsync():
    """Rollback must restore files via rsync."""
    content = ROLLBACK_SCRIPT.read_text()
    assert "rsync" in content, "rollback-deployed.sh must use rsync to restore"


def test_rollback_script_restarts_service():
    """Rollback must restart the systemd service after restore."""
    content = ROLLBACK_SCRIPT.read_text()
    assert "systemctl restart" in content, "rollback-deployed.sh must restart the service"
