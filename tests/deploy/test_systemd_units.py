"""Tests for systemd unit files and drop-ins (issue #707)."""
import configparser
from pathlib import Path
import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy"


def _parse_unit(path):
    """Parse a systemd unit / drop-in file into a ConfigParser.

    Systemd unit files use [Unit], [Service], [Install] sections.
    Drop-ins typically use [Unit] or [Service] sections too.
    We do NOT prepend [DEFAULT] because these files already have proper sections.
    """
    cp = configparser.ConfigParser(strict=False)
    cp.read_string(path.read_text())
    return cp


def _get_from_any_section(cp, key, fallback=""):
    """Return the value of a key searching across all sections."""
    for section in cp.sections():
        val = cp.get(section, key, fallback=None)
        if val is not None:
            return val
    return fallback


def test_dashboard_service_has_watchdog():
    svc = _parse_unit(DEPLOY_DIR / "runner-dashboard.service")
    val = _get_from_any_section(svc, "WatchdogSec", "")
    assert val != "", "WatchdogSec must be set in runner-dashboard.service"


def test_autoscaler_service_has_watchdog():
    svc = _parse_unit(DEPLOY_DIR / "runner-autoscaler.service")
    val = _get_from_any_section(svc, "WatchdogSec", "")
    assert val != "", "WatchdogSec must be set in runner-autoscaler.service"


def test_restart_burst_dropin_exists():
    dropin = DEPLOY_DIR / "systemd-dropins" / "10-restart-burst.conf"
    assert dropin.exists(), f"Missing drop-in: {dropin}"


def test_restart_burst_dropin_has_limits():
    dropin = DEPLOY_DIR / "systemd-dropins" / "10-restart-burst.conf"
    cp = _parse_unit(dropin)
    val = _get_from_any_section(cp, "StartLimitIntervalSec", "")
    assert val != "", "StartLimitIntervalSec missing"
    assert int(val) >= 300, f"StartLimitIntervalSec too short: {val}"
    burst = _get_from_any_section(cp, "StartLimitBurst", "")
    assert burst != "", "StartLimitBurst missing"
    assert 3 <= int(burst) <= 10, f"StartLimitBurst out of sane range: {burst}"
