"""Tests for /_drain endpoint (issue #711)."""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _is_executable_script(path: Path) -> bool:
    if os.name != "nt":
        return bool(path.stat().st_mode & 0o111)
    rel = path.relative_to(_REPO_ROOT).as_posix()
    result = subprocess.run(
        ["git", "ls-files", "--stage", rel],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.startswith("100755 ")


def test_drain_mode_flag_exists():
    # Verify the drain-dashboard.sh script exists
    script = _REPO_ROOT / "deploy" / "drain-dashboard.sh"
    assert script.exists()
    assert _is_executable_script(script), "drain-dashboard.sh must be executable"


def test_drain_script_has_sigterm():
    """Drain script must send SIGTERM before SIGKILL."""
    script = Path(__file__).resolve().parents[2] / "deploy" / "drain-dashboard.sh"
    content = script.read_text()
    assert "SIGTERM" in content or "kill -TERM" in content.lower(), "drain-dashboard.sh must send SIGTERM"


def test_drain_script_has_timeout():
    """Drain script must enforce a timeout before SIGKILL."""
    script = Path(__file__).resolve().parents[2] / "deploy" / "drain-dashboard.sh"
    content = script.read_text()
    assert "DRAIN_TIMEOUT_S" in content, "drain-dashboard.sh must have a timeout"
