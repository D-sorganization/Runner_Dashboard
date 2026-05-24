"""Tests for runner memory drop-in (issue #711)."""

import configparser
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy"


def _parse_unit(path):
    cp = configparser.ConfigParser(strict=False)
    cp.read_string(path.read_text())
    return cp


def _get_from_any_section(cp, key, fallback=""):
    for section in cp.sections():
        val = cp.get(section, key, fallback=None)
        if val is not None:
            return val
    return fallback


def test_runner_memory_dropin_exists():
    dropin = DEPLOY_DIR / "systemd-dropins" / "10-runner-memory.conf"
    assert dropin.exists()


def test_runner_memory_dropin_has_memory_max():
    dropin = DEPLOY_DIR / "systemd-dropins" / "10-runner-memory.conf"
    cp = _parse_unit(dropin)
    val = _get_from_any_section(cp, "MemoryMax", "")
    assert val, "MemoryMax must be set"
    # Value must be parseable (e.g., "2G")
    assert val.endswith(("G", "M", "K", "%")) or val.isdigit()


def test_runner_memory_dropin_has_tasks_max():
    dropin = DEPLOY_DIR / "systemd-dropins" / "10-runner-memory.conf"
    cp = _parse_unit(dropin)
    val = _get_from_any_section(cp, "TasksMax", "")
    assert val, "TasksMax must be set"
    assert int(val) >= 1024


def test_drain_dropin_exists():
    """ExecStop drain drop-in must exist (issue #711)."""
    dropin = DEPLOY_DIR / "systemd-dropins" / "20-drain-hook.conf"
    assert dropin.exists(), f"Missing drop-in: {dropin}"


def test_drain_dropin_has_exec_stop():
    dropin = DEPLOY_DIR / "systemd-dropins" / "20-drain-hook.conf"
    content = dropin.read_text()
    assert "ExecStop" in content, "20-drain-hook.conf must have ExecStop"
    assert "TimeoutStopSec" in content, "20-drain-hook.conf must have TimeoutStopSec"
