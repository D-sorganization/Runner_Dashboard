"""Tests for /_drain endpoint (issue #711)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


def test_drain_mode_flag_exists():
    # Verify the drain-dashboard.sh script exists
    script = Path(__file__).resolve().parents[2] / "deploy" / "drain-dashboard.sh"
    assert script.exists()
    assert script.stat().st_mode & 0o111, "drain-dashboard.sh must be executable"


def test_drain_script_has_sigterm():
    """Drain script must send SIGTERM before SIGKILL."""
    script = Path(__file__).resolve().parents[2] / "deploy" / "drain-dashboard.sh"
    content = script.read_text()
    assert "SIGTERM" in content or "kill -TERM" in content.lower(), \
        "drain-dashboard.sh must send SIGTERM"


def test_drain_script_has_timeout():
    """Drain script must enforce a timeout before SIGKILL."""
    script = Path(__file__).resolve().parents[2] / "deploy" / "drain-dashboard.sh"
    content = script.read_text()
    assert "DRAIN_TIMEOUT_S" in content, "drain-dashboard.sh must have a timeout"
