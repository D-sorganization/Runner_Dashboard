"""Tests for update-deployed.sh pre-flight validation (issue #713)."""
import subprocess
import sys
from pathlib import Path

import pytest
import tempfile
import shutil

DEPLOY_DIR_ROOT = Path(__file__).resolve().parents[2]
UPDATE_SCRIPT = DEPLOY_DIR_ROOT / "deploy" / "update-deployed.sh"
ROLLBACK_SCRIPT = DEPLOY_DIR_ROOT / "deploy" / "rollback-deployed.sh"


def test_update_script_exists():
    assert UPDATE_SCRIPT.exists()


def test_rollback_script_exists():
    assert ROLLBACK_SCRIPT.exists()


def test_rollback_script_is_executable():
    assert ROLLBACK_SCRIPT.stat().st_mode & 0o111


def test_update_script_has_preflight_check():
    content = UPDATE_SCRIPT.read_text()
    assert "py_compile" in content or "pre-flight" in content.lower(), \
        "update-deployed.sh must have pre-flight syntax check"


def test_update_script_has_rollback_trigger():
    content = UPDATE_SCRIPT.read_text()
    assert "rollback" in content.lower(), \
        "update-deployed.sh must trigger rollback on failure"


def test_rollback_script_has_rsync():
    """Rollback must restore files via rsync."""
    content = ROLLBACK_SCRIPT.read_text()
    assert "rsync" in content, "rollback-deployed.sh must use rsync to restore"


def test_rollback_script_restarts_service():
    """Rollback must restart the systemd service after restore."""
    content = ROLLBACK_SCRIPT.read_text()
    assert "systemctl restart" in content, \
        "rollback-deployed.sh must restart the service"
