"""rd-barb notify delivery health: a failed delivery is visible, degraded after 5 minutes (RD #3075)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff import rd_barb_notify_health as nh

T0 = 1_800_000_000.0


@pytest.fixture
def health(tmp_path: Path) -> nh.NotifyHealth:
    return nh.NotifyHealth(tmp_path / "health.json")


def test_no_attempt_yet_reads_never_attempted(health: nh.NotifyHealth) -> None:
    status = health.status(now=T0)
    assert status["state"] == "never_attempted"
    assert status["consecutive_failures"] == 0 and status["failing_since"] is None


def test_success_reads_ok_with_channel(health: nh.NotifyHealth) -> None:
    health.record(ok=True, channel="email", now=T0)
    status = health.status(now=T0 + 10)
    assert status["state"] == "ok" and status["channel"] == "email" and status["last_success_at"] == T0


def test_failure_is_visible_at_once_and_degraded_only_at_five_minutes(health: nh.NotifyHealth) -> None:
    health.record(ok=False, channel="none", error="no_channel_configured", now=T0)
    first = health.status(now=T0 + nh.DEGRADED_AFTER_S - 1)
    assert first["state"] == "failing" and first["last_error"] == "no_channel_configured"
    assert first["failing_since"] == T0 and first["consecutive_failures"] == 1
    assert health.status(now=T0 + nh.DEGRADED_AFTER_S)["state"] == "degraded"


def test_failing_since_keeps_the_first_failure(health: nh.NotifyHealth) -> None:
    health.record(ok=False, channel="none", error="a", now=T0)
    health.record(ok=False, channel="none", error="b", now=T0 + 60)
    status = health.status(now=T0 + 61)
    assert status["failing_since"] == T0 and status["consecutive_failures"] == 2 and status["last_error"] == "b"


def test_one_success_clears_the_failure(health: nh.NotifyHealth) -> None:
    health.record(ok=False, channel="none", error="x", now=T0)
    health.record(ok=True, channel="email", now=T0 + 400)
    status = health.status(now=T0 + 401)
    assert status["state"] == "ok" and status["failing_since"] is None and status["consecutive_failures"] == 0


def test_unreadable_file_reads_unknown_not_ok(tmp_path: Path) -> None:
    path = tmp_path / "h.json"
    path.write_text("{not json", encoding="utf-8")
    assert nh.NotifyHealth(path).status(now=T0)["state"] == "unknown"


def test_hand_edited_file_reads_unknown(tmp_path: Path) -> None:
    path = tmp_path / "h.json"
    path.write_text('{"last_attempt_at": 1, "failing_since": "soon", "consecutive_failures": 1}', encoding="utf-8")
    assert nh.NotifyHealth(path).status(now=T0)["state"] == "unknown"


def test_error_is_one_bounded_line(health: nh.NotifyHealth) -> None:
    health.record(ok=False, channel="none", error="line1\nline2 " + "x" * 500, now=T0)
    error = health.status(now=T0)["last_error"]
    assert "\n" not in error and len(error) <= nh.MAX_ERROR_CHARS


def test_preconditions(health: nh.NotifyHealth) -> None:
    with pytest.raises(ValueError, match="channel"):
        health.record(ok=True, channel="", now=T0)
    with pytest.raises(ValueError, match="error"):
        health.record(ok=False, channel="none", error="", now=T0)


def test_default_path_follows_the_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    assert nh.default_health_path() == tmp_path / "rd-barb" / "notify-health.json"
