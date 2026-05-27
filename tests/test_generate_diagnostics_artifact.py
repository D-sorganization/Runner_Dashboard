"""Tests for operator diagnostics, sharing violation detection, and artifact generation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

# Add backend directory to path
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import generate_diagnostics_artifact  # noqa: E402
from routers import diagnostics  # noqa: E402


def test_detect_sharing_violations_clean() -> None:
    """If no files are locked, detect_sharing_violations should report no issues."""
    with patch("pathlib.Path.exists", return_value=False):
        res = diagnostics.detect_sharing_violations()
        assert res["detected"] is False
        assert res["error_code"] is None


def test_detect_sharing_violations_locked_db() -> None:
    """If a database file raises PermissionError with winerror 32, detect sharing violation."""
    error = PermissionError("[Errno 13] Permission denied")
    error.winerror = 32

    # Patch Path.exists to say it exists, and open to raise error
    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("builtins.open", mock_open()) as mock_file,
    ):
        mock_file.side_effect = error
        res = diagnostics.detect_sharing_violations()
        assert res["detected"] is True
        assert res["error_code"] == "ERROR_SHARING_VIOLATION"
        assert "locked by another process" in res["message"]


def test_detect_sharing_violations_locked_vhdx_stopped_distro() -> None:
    """If a VHDX file is locked while the distro is not running, detect sharing violation."""
    error = PermissionError("[Errno 13] Permission denied")
    error.winerror = 32

    wsl_vhdx_status = [
        {
            "Distribution": "Ubuntu",
            "Path": "C:\\WSL\\ext4.vhdx",
            "Attached": True,
        }
    ]
    # Distro is stopped (not listed as running)
    wsl_status_str = "  NAME      STATE           VERSION\n* Ubuntu    Stopped         2"

    def open_side_effect(file_path, *args, **kwargs):
        if "ext4.vhdx" in str(file_path):
            raise error
        return MagicMock()

    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("builtins.open", side_effect=open_side_effect),
    ):
        res = diagnostics.detect_sharing_violations(wsl_vhdx_status, wsl_status_str)
        assert res["detected"] is True
        assert res["error_code"] == "ERROR_SHARING_VIOLATION"
        assert "ext4.vhdx" in res["target_file"]
        assert "Ubuntu" in res["message"]


def test_generate_markdown_artifact_content() -> None:
    """Verify markdown content generation compiles all diagnostics fields."""
    summary = {
        "hostname": "test-host",
        "wsl_available": True,
        "wsl_status": "Ubuntu Running",
        "wsl_vhdx_status": [
            {
                "Distribution": "Ubuntu",
                "Path": "C:\\WSL\\ext4.vhdx",
                "Attached": True,
            }
        ],
        "storage_handle_incident": {
            "detected": True,
            "error_code": "ERROR_SHARING_VIOLATION",
            "target_file": "replay.db",
            "message": "replay.db is locked",
        },
        "dashboard_pid": 1234,
        "dashboard_memory_mb": 42.1,
        "dashboard_port": 8321,
        "git_commit": "abcdef",
        "is_drifted": True,
        "drift_details": "Local changes exist",
    }

    markdown = diagnostics.generate_markdown_artifact_content(summary)
    assert "# Runner Dashboard Diagnostics Artifact" in markdown
    assert "test-host" in markdown
    assert "Ubuntu Running" in markdown
    assert "C:\\WSL\\ext4.vhdx" in markdown
    assert "ERROR_SHARING_VIOLATION" in markdown
    assert "replay.db" in markdown
    assert "abcdef" in markdown
    assert "Local changes exist" in markdown
    assert "docs/runbooks/wsl-vhdx-compaction.md" in markdown


def test_standalone_diagnostics_report() -> None:
    """Verify that standalone script compile markdown functions correctly."""
    sys_info = {
        "time": "2026-05-27T12:00:00Z",
        "hostname": "cli-host",
        "platform": "win32",
        "python_version": "3.11.0",
        "disk_space": {
            "WSL Root": {
                "total_gb": 100,
                "used_gb": 40,
                "free_gb": 60,
                "percent_used": 40,
            }
        },
    }
    git_info = {
        "branch": "main",
        "commit": "1234567",
        "status": "clean",
        "diff_summary": "1 file changed",
    }
    wsl_status = "Ubuntu Stopped"
    vhdx_status = [{"Distribution": "Ubuntu", "Path": "C:\\ext4.vhdx", "Attached": False}]
    sharing_violations = {
        "detected": False,
    }
    process_info = "[]"
    logs = "Log line 1\nLog line 2"

    markdown = generate_diagnostics_artifact.generate_markdown(
        sys_info,
        git_info,
        wsl_status,
        vhdx_status,
        sharing_violations,
        process_info,
        logs,
    )

    assert "# Runner Dashboard Crash Diagnostics Report" in markdown
    assert "cli-host" in markdown
    assert "WSL Root" in markdown
    assert "Ubuntu Stopped" in markdown
    assert "C:\\ext4.vhdx" in markdown
    assert "1234567" in markdown
    assert "Log line 1" in markdown
    assert "Safe Compaction Flow Instructions" in markdown
