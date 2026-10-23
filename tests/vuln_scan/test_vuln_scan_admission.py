"""Scan admission: VM-only real scans, idle CI, window, slot, budget (#2572)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from vuln_scan.admission import Facts, admit, in_window, runtime_problems
from vuln_scan.operator_config import ScanHost, parse_operator_config

SHA = "0bb24108aa" * 4
VM = ScanHost("scanvm", "vm", "ssh://scan@scanvm", "vs-build")
# 02:00 in Los Angeles (PDT, UTC-7) on 2026-10-23.
NIGHT = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
CONFIG = parse_operator_config(
    {
        "repos": {"Tools": {"enabled": True}},
        "scan_hosts": {"scanvm": {"isolation": "vm", "docker_host": "ssh://scan@scanvm", "build_network": "vs-build"}},
        "executor": {"cli_version": "2.1.263"},
    }
)
GOOD = Facts(
    now=NIGHT,
    host=VM,
    contract_valid=True,
    default_branch_sha=SHA,
    free_gb=200.0,
    build_filter_ok=True,
    queued_jobs=0,
    slot_free=True,
    spent_usd_today=0.0,
    oauth_ok=True,
)


def test_good_facts_admit() -> None:
    assert CONFIG.errors == ()
    assert admit(CONFIG, "Tools", SHA, GOOD) == []


def test_default_facts_refuse_everything() -> None:
    reasons = admit(CONFIG, "Tools", SHA, Facts(now=NIGHT))
    assert len(reasons) >= 7


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ({"host": None}, "no scan host"),
        ({"host": ScanHost("brick", "container", "ssh://x@brick", "vs-build")}, "'vm'"),
        ({"host": ScanHost("ControlTower-vm", "vm", "ssh://x@ct", "vs-build")}, "ControlTower"),
        ({"host": ScanHost("vm2", "vm", "ssh://x@controltower", "vs-build")}, "ControlTower"),
        ({"host": ScanHost("vm2", "vm", None, "vs-build")}, "docker_host"),
        ({"host": ScanHost("vm2", "vm", "ssh://x@vm2", None)}, "build_network"),
        ({"contract_valid": False}, "contract"),
        ({"contract_disabled": True}, "disabled: true"),
        ({"build_filter_ok": False}, "build filter is not live"),
        ({"default_branch_sha": "1" * 40}, "default-branch"),
        ({"default_branch_sha": None}, "default-branch"),
        ({"free_gb": 59.9}, "free disk"),
        ({"free_gb": None}, "free disk"),
        ({"queued_jobs": 1}, "CI queue"),
        ({"queued_jobs": None}, "CI queue"),
        ({"now": datetime(2026, 10, 23, 20, 0, tzinfo=UTC)}, "window"),
        ({"slot_free": False}, "slot"),
        ({"spent_usd_today": 5.0}, "budget"),
        ({"oauth_ok": False}, "subscription"),
    ],
)
def test_each_rule_refuses(change: dict, needle: str) -> None:
    reasons = admit(CONFIG, "Tools", SHA, replace(GOOD, **change))
    assert len(reasons) == 1 and needle in reasons[0], reasons


def test_repo_and_pin_come_from_config() -> None:
    assert "not enabled" in admit(CONFIG, "UpstreamDrift", SHA, GOOD)[0]
    unpinned = parse_operator_config({"repos": {"Tools": {"enabled": True}}})
    assert any("not pinned" in r for r in admit(unpinned, "Tools", SHA, GOOD))


def test_window_wraps_midnight() -> None:
    assert in_window(datetime(2026, 1, 1, 7, 30, tzinfo=UTC), "22:00", "06:00", "UTC") is False
    assert in_window(datetime(2026, 1, 1, 23, 0, tzinfo=UTC), "22:00", "06:00", "UTC")
    assert in_window(datetime(2026, 1, 1, 5, 59, tzinfo=UTC), "22:00", "06:00", "UTC")
    assert in_window(NIGHT, "01:00", "06:00", "Not/AZone") is False


def test_runtime_recheck_yields_to_ci() -> None:
    assert runtime_problems(CONFIG, Facts(now=NIGHT, queued_jobs=0, free_gb=100)) == []
    assert "yielding to CI" in runtime_problems(CONFIG, Facts(now=NIGHT, queued_jobs=3))[0]
    assert runtime_problems(CONFIG, Facts(now=NIGHT, queued_jobs=None))


def test_runtime_recheck_aborts_when_free_disk_is_unknown() -> None:
    problems = runtime_problems(CONFIG, Facts(now=NIGHT, queued_jobs=0, free_gb=None))
    assert problems == ["scan host free disk fell to None GB"]


def test_filter_reason_is_only_given_for_an_otherwise_good_host() -> None:
    bad_host = replace(GOOD, host=ScanHost("vm2", "vm", None, "vs-build"), build_filter_ok=False)
    assert not any("build filter" in r for r in admit(CONFIG, "Tools", SHA, bad_host))
