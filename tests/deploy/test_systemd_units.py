"""Static contract tests for the systemd unit files (A1 — restart-burst).

These tests are deliberately filesystem-only: they parse the .service files
shipped in deploy/ as plain text and assert the directives we rely on for
crash-loop containment and watchdog supervision are present and within sane
bounds. They do not require systemd to be installed.

The DRY/LoD principle here: every unit file directive that the dashboard or
autoscaler relies on for stability is encoded once, in this test, so that a
future PR can't silently drop it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy"

UNITS_UNDER_TEST: tuple[str, ...] = (
    "runner-dashboard.service",
    "runner-autoscaler.service",
)


def _parse_unit(path: Path) -> dict[str, str]:
    """Return a flat dict of key→last-value for a systemd unit file.

    Multi-key directives (e.g. multiple `Environment=`) are folded to the
    *last* occurrence. We only assert on the single-valued directives below,
    so this is acceptable.
    """
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


@pytest.mark.parametrize("unit_file", UNITS_UNDER_TEST)
def test_unit_file_exists(unit_file: str) -> None:
    """Pre-condition for every other assertion in this module."""
    assert (DEPLOY_DIR / unit_file).is_file(), f"missing unit file: {unit_file}"


@pytest.mark.parametrize("unit_file", UNITS_UNDER_TEST)
def test_unit_declares_start_limit_interval(unit_file: str) -> None:
    """A1: every supervised unit must cap restart bursts to prevent silent
    crash-loops respawning forever. StartLimitIntervalSec defines the rolling
    window; a sane bound is 60s ≤ window ≤ 3600s.
    """
    cfg = _parse_unit(DEPLOY_DIR / unit_file)
    raw = cfg.get("StartLimitIntervalSec")
    assert raw is not None, f"{unit_file} must declare StartLimitIntervalSec for restart-burst limits"
    # accept either bare seconds or "<n>s"
    seconds = int(re.sub(r"\D", "", raw))
    assert 60 <= seconds <= 3600, f"{unit_file}: StartLimitIntervalSec={raw} outside [60s, 3600s]"


@pytest.mark.parametrize("unit_file", UNITS_UNDER_TEST)
def test_unit_declares_start_limit_burst(unit_file: str) -> None:
    """A1: StartLimitBurst must be present and small enough that a real bug
    surfaces instead of being masked by infinite respawning. Bound: 1 ≤ N ≤ 10.
    """
    cfg = _parse_unit(DEPLOY_DIR / unit_file)
    raw = cfg.get("StartLimitBurst")
    assert raw is not None, f"{unit_file} must declare StartLimitBurst for restart-burst limits"
    n = int(raw)
    assert 1 <= n <= 10, f"{unit_file}: StartLimitBurst={n} outside [1, 10]"


@pytest.mark.parametrize("unit_file", UNITS_UNDER_TEST)
def test_unit_declares_watchdog_sec(unit_file: str) -> None:
    """A1: WatchdogSec must be set so systemd kills a hung process. The
    in-process watchdog heartbeat (sd_notify WATCHDOG=1) is wired in code; the
    unit-file assertion ensures the kernel-side enforcement exists too.
    """
    cfg = _parse_unit(DEPLOY_DIR / unit_file)
    raw = cfg.get("WatchdogSec")
    assert raw is not None, f"{unit_file} must declare WatchdogSec"
    seconds = int(re.sub(r"\D", "", raw))
    assert 30 <= seconds <= 600, f"{unit_file}: WatchdogSec={raw} outside [30s, 600s]"


def test_dashboard_unit_is_notify_type() -> None:
    """The dashboard unit must use Type=notify so READY=1 and WATCHDOG=1
    notifications take effect. Without this, WatchdogSec is a no-op.
    """
    cfg = _parse_unit(DEPLOY_DIR / "runner-dashboard.service")
    assert cfg.get("Type") == "notify", "runner-dashboard.service must declare Type=notify"


@pytest.mark.parametrize("unit_file", UNITS_UNDER_TEST)
def test_unit_burst_window_is_self_consistent(unit_file: str) -> None:
    """A1: StartLimitBurst should be reachable within StartLimitIntervalSec
    given the unit's own RestartSec — otherwise the burst-limit can never
    actually trigger.

    Constraint: burst * RestartSec ≤ interval. We allow equality to give
    operators the maximum window before systemd gives up.
    """
    cfg = _parse_unit(DEPLOY_DIR / unit_file)
    interval = int(re.sub(r"\D", "", cfg["StartLimitIntervalSec"]))
    burst = int(cfg["StartLimitBurst"])
    restart_sec = int(re.sub(r"\D", "", cfg.get("RestartSec", "5")))
    # Pre-condition: numbers are positive.
    assert restart_sec > 0 and interval > 0 and burst > 0
    # Post-condition: burst is reachable.
    assert burst * restart_sec <= interval, (
        f"{unit_file}: burst={burst} × RestartSec={restart_sec}s "
        f"exceeds StartLimitIntervalSec={interval}s — burst can never trigger"
    )
