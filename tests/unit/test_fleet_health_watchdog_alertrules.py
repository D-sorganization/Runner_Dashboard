"""Structural checks for the notify-only Alertmanager rules file (#3934 S2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_RULES_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "observability" / "fleet-health-watchdog.alertrules.yml"
)


def _load_rules() -> list[dict]:
    doc = yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8"))
    group = doc["groups"][0]
    assert group["name"] == "fleet-health-watchdog"
    return group["rules"]


def test_rules_file_is_valid_yaml_with_one_group() -> None:
    doc = yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8"))
    assert list(doc.keys()) == ["groups"]
    assert len(doc["groups"]) == 1


def test_every_rule_is_notify_only_and_references_a_watchdog_metric() -> None:
    rules = _load_rules()
    assert len(rules) >= 2
    for rule in rules:
        assert rule["labels"]["notify_only"] == "true"
        assert rule["expr"].startswith("rd_host_")
        assert rule["annotations"]["summary"]


def test_disk_fill_alert_expr_matches_the_exported_metric_name() -> None:
    rules = {rule["alert"]: rule for rule in _load_rules()}
    assert "rd_host_disk_fill_hours_to_full" in rules["RDHostDiskFillProjected"]["expr"]
    assert "rd_host_metric_anomaly" in rules["RDHostMetricAnomaly"]["expr"]
