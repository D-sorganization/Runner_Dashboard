"""Tests for expiring runner target override contract (#4308)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import runner_target_contract as rtc


def test_clamp_runners_uses_host_defaults() -> None:
    assert rtc.clamp_runners(99, "hub-a") == 6
    assert rtc.clamp_runners(4, "node-b") == 4
    assert rtc.clamp_runners(10, "node-a") == 8


def test_host_runner_cap_uses_min_of_registry_and_hard_cap(monkeypatch) -> None:
    registry = {
        "machines": [
            {
                "name": "hub-a",
                "runners": {"max": 16},
            }
        ]
    }

    monkeypatch.setattr(rtc, "load_machine_registry", lambda: registry)
    assert rtc.host_runner_cap("hub-a") == 6


def test_override_expires_with_fake_clock(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "target-override.json"
    start = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    until = (start + timedelta(hours=1)).isoformat()
    rtc.write_target_override(
        runners=3,
        reason="test",
        actor="pytest",
        before=6,
        until=until,
        path=path,
        created_at=start,
    )

    tick = [start]

    def now() -> datetime:
        return tick[0]

    assert rtc.load_target_override(path, now=now)["runners"] == 3
    tick[0] = start + timedelta(hours=2)
    assert rtc.load_target_override(path, now=now) is None
    assert not path.exists()


def test_parse_until_accepts_hhmm() -> None:
    anchor = datetime(2026, 11, 10, 17, 30, tzinfo=UTC)
    parsed = rtc.parse_until("18:00", now=anchor)
    assert parsed is not None
    assert parsed.hour == 18 and parsed.minute == 0
