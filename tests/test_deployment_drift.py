"""Tests for backend/deployment_drift.py — issue #386."""

from __future__ import annotations

from pathlib import Path

import deployment_drift as dd

# ---------------------------------------------------------------------------
# _parse_version
# ---------------------------------------------------------------------------


def test_parse_version_happy() -> None:
    assert dd._parse_version("1.2.3") == (1, 2, 3)


def test_parse_version_v_prefix() -> None:
    assert dd._parse_version("v2.0.1") == (2, 0, 1)


def test_parse_version_unknown() -> None:
    assert dd._parse_version("unknown") is None


def test_parse_version_empty() -> None:
    assert dd._parse_version("") is None


def test_parse_version_too_short() -> None:
    assert dd._parse_version("1.2") is None


def test_parse_version_non_numeric() -> None:
    assert dd._parse_version("1.2.alpha") is None


# ---------------------------------------------------------------------------
# _classify_severity
# ---------------------------------------------------------------------------


def test_classify_severity_none() -> None:
    assert dd._classify_severity((1, 2, 3), (1, 2, 3)) == "none"


def test_classify_severity_patch() -> None:
    assert dd._classify_severity((1, 2, 3), (1, 2, 4)) == "patch"


def test_classify_severity_minor() -> None:
    assert dd._classify_severity((1, 2, 3), (1, 3, 0)) == "minor"


def test_classify_severity_major() -> None:
    assert dd._classify_severity((1, 2, 3), (2, 0, 0)) == "major"


def test_classify_severity_unknown_current() -> None:
    assert dd._classify_severity(None, (1, 2, 3)) == "unknown"


def test_classify_severity_unknown_expected() -> None:
    assert dd._classify_severity((1, 2, 3), None) == "unknown"


# ---------------------------------------------------------------------------
# read_expected_version
# ---------------------------------------------------------------------------


def test_read_expected_version_normal(tmp_path: Path) -> None:
    f = tmp_path / "VERSION"
    f.write_text("3.7.2\n", encoding="utf-8")
    assert dd.read_expected_version(f) == "3.7.2"


def test_read_expected_version_comment_lines(tmp_path: Path) -> None:
    f = tmp_path / "VERSION"
    f.write_text("# comment\n4.0.0\n", encoding="utf-8")
    assert dd.read_expected_version(f) == "4.0.0"


def test_read_expected_version_missing(tmp_path: Path) -> None:
    assert dd.read_expected_version(tmp_path / "MISSING") == "unknown"


def test_read_expected_version_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "VERSION"
    f.write_text("", encoding="utf-8")
    assert dd.read_expected_version(f) == "unknown"


# ---------------------------------------------------------------------------
# evaluate_drift — happy paths
# ---------------------------------------------------------------------------


def test_evaluate_drift_up_to_date() -> None:
    status = dd.evaluate_drift({"version": "1.2.3"}, "1.2.3")
    assert status.drift is False
    assert status.severity == "none"
    assert "up to date" in status.message


def test_evaluate_drift_patch() -> None:
    status = dd.evaluate_drift({"version": "1.2.3"}, "1.2.4")
    assert status.drift is True
    assert status.severity == "patch"
    assert "patch" in status.message


def test_evaluate_drift_minor() -> None:
    status = dd.evaluate_drift({"version": "1.2.3"}, "1.3.0")
    assert status.drift is True
    assert status.severity == "minor"


def test_evaluate_drift_major() -> None:
    status = dd.evaluate_drift({"version": "1.0.0"}, "2.0.0")
    assert status.drift is True
    assert status.severity == "major"


def test_evaluate_drift_dirty() -> None:
    status = dd.evaluate_drift({"version": "1.0.0", "git_dirty": True}, "1.0.0")
    assert status.drift is True
    assert status.severity == "dirty"
    assert "dirty" in status.message.lower()


def test_evaluate_drift_unknown_version() -> None:
    status = dd.evaluate_drift({"version": "unknown"}, "1.2.3")
    assert status.drift is True
    assert status.severity == "unknown"


def test_evaluate_drift_missing_deployment() -> None:
    status = dd.evaluate_drift(None, "1.0.0")
    assert status.drift is True


def test_evaluate_drift_malformed_version() -> None:
    status = dd.evaluate_drift({"version": "not-a-version"}, "1.2.3")
    assert status.drift is True
    assert status.severity == "unknown"


# ---------------------------------------------------------------------------
# DriftStatus.to_dict
# ---------------------------------------------------------------------------


def test_drift_status_to_dict_keys() -> None:
    status = dd.evaluate_drift({"version": "1.0.0"}, "1.0.0")
    d = status.to_dict()
    for key in ("current", "expected", "drift", "severity", "dirty", "git_sha", "message", "update_available"):
        assert key in d, f"Missing key: {key}"


def test_drift_status_update_available_false_when_no_drift() -> None:
    status = dd.evaluate_drift({"version": "2.0.0"}, "2.0.0")
    assert status.to_dict()["update_available"] is False


def test_drift_status_update_available_true_on_patch() -> None:
    status = dd.evaluate_drift({"version": "2.0.0"}, "2.0.1")
    assert status.to_dict()["update_available"] is True


# ---------------------------------------------------------------------------
# emit_update_signal
# ---------------------------------------------------------------------------


def test_emit_update_signal_returns_dict() -> None:
    status = dd.evaluate_drift({"version": "1.0.0"}, "2.0.0")
    event = dd.emit_update_signal("my-node", status)
    assert event["node"] == "my-node"
    assert event["event"] == "dashboard.node.update_requested"
    assert event["current"] == "1.0.0"
    assert event["expected"] == "2.0.0"


def test_emit_update_signal_custom_reason() -> None:
    status = dd.evaluate_drift({"version": "1.0.0"}, "1.0.1")
    event = dd.emit_update_signal("host-1", status, reason="scheduled")
    assert event["reason"] == "scheduled"
