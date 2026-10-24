"""Operator settings for the security scanner (``config/security_scan.json``, #2569)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from vuln_scan.operator_config import (
    DEFAULT_PATH,
    MIN_FREE_GB_FLOOR,
    load_operator_config,
    parse_operator_config,
)


def test_shipped_config_is_clean_and_safe() -> None:
    config = load_operator_config(DEFAULT_PATH)
    assert config.errors == ()
    # Nothing is enabled and no host can run real scans until the owner says so.
    assert not any(s.enabled for s in config.repos.values())
    assert not config.defaults.enabled
    assert config.scan_hosts == ()


def test_missing_file_is_safe_defaults(tmp_path: Path) -> None:
    config = load_operator_config(tmp_path / "nope.json")
    assert config.repos == {} and not config.defaults.enabled
    assert "not found" in config.errors[0]


def test_malformed_file_never_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert "unreadable" in load_operator_config(path).errors[0]
    assert parse_operator_config([1, 2]).errors == ("config root must be an object",)


def test_repo_settings_inherit_defaults() -> None:
    config = parse_operator_config({"defaults": {"full_scan_days": 14}, "repos": {"Tools": {"enabled": True}}})
    assert config.repo("Tools").enabled and config.repo("Tools").full_scan_days == 14
    assert not config.repo("Unlisted").enabled


def test_min_free_gb_cannot_go_below_floor() -> None:
    config = parse_operator_config({"min_free_gb": 10})
    assert config.min_free_gb == MIN_FREE_GB_FLOOR
    assert any("safety floor" in e for e in config.errors)


def test_only_vm_hosts_are_eligible_for_real_scans() -> None:
    config = parse_operator_config(
        {
            "scan_hosts": {
                "scanvm": {"isolation": "vm", "docker_host": "ssh://scan@scanvm"},
                "brick": {"isolation": "container"},
                "bad": {"isolation": "wsl"},
                "evil": {"isolation": "vm", "docker_host": "tcp://0.0.0.0:2375"},
            }
        }
    )
    hosts = {h.name: h for h in config.scan_hosts}
    assert set(hosts) == {"scanvm", "brick"}
    assert hosts["scanvm"].eligible_for_real_scans
    assert not hosts["brick"].eligible_for_real_scans
    assert len(config.errors) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"repos": {"bad name!": {}}},
        {"repos": {"Tools": {"enabled": "yes"}}},
        {"defaults": {"priority": 99}},
        {"window": {"start": "25:00", "end": "06:00"}},
        {"usd_per_day": -1},
        {"max_new_findings_per_scan": 0},
    ],
)
def test_bad_values_are_reported(payload: dict) -> None:
    assert parse_operator_config(payload).errors


def test_to_dict_round_trips_through_json() -> None:
    body = load_operator_config(DEFAULT_PATH).to_dict()
    assert json.loads(json.dumps(body))["min_free_gb"] == MIN_FREE_GB_FLOOR
