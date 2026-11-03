"""Hub scheduler pause TTL (#3821)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from staff import scheduler_pause as sp


@pytest.fixture(autouse=True)
def _reset_debounce() -> None:
    sp.reset_pause_alert_debounce_for_tests()
    yield
    sp.reset_pause_alert_debounce_for_tests()


@pytest.mark.unit
def test_pause_does_not_apply_on_worker_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sp, "MACHINE_ROLE", "node")
    status = sp.evaluate_pause(scheduler_enabled=False)
    assert status.applies is False
    assert status.expired is False


@pytest.mark.unit
def test_hub_pause_gets_ttl_from_state_when_env_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sp, "MACHINE_ROLE", "hub")
    monkeypatch.setenv("STAFF_SCHEDULER_PAUSE_STATE", str(tmp_path / "pause.json"))
    monkeypatch.delenv(sp.PAUSE_UNTIL_ENV, raising=False)
    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    status = sp.evaluate_pause(scheduler_enabled=False, now=now)
    assert status.applies is True
    assert status.source == "state_ttl"
    assert status.pause_until is not None
    until = datetime.fromisoformat(status.pause_until.replace("Z", "+00:00"))
    assert until > now
    assert not status.expired


@pytest.mark.unit
def test_hub_pause_expired_raises_alert_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sp, "MACHINE_ROLE", "hub")
    monkeypatch.setenv("STAFF_SCHEDULER_PAUSE_UNTIL", "2026-11-01T00:00:00Z")
    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    alert = sp.pause_expired_alert(now=now)
    assert alert is not None
    assert alert["key"] == "scheduler-pause-expired"

    events: list[str] = []

    class _Store:
        def record(self, ev: object) -> None:
            events.append(getattr(ev, "title", ""))

    monkeypatch.setattr("fleet_events.get_event_store", lambda: _Store())
    monkeypatch.setattr("push.notify", lambda *a, **k: None)

    assert sp.emit_pause_expired_alert(now=now) is True
    assert sp.emit_pause_expired_alert(now=now) is False
    assert len(events) == 1


@pytest.mark.unit
def test_enabled_scheduler_clears_pause_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sp, "MACHINE_ROLE", "hub")
    state = tmp_path / "pause.json"
    state.write_text('{"pause_until":"2099-01-01T00:00:00Z"}', encoding="utf-8")
    monkeypatch.setenv("STAFF_SCHEDULER_PAUSE_STATE", str(state))
    sp.evaluate_pause(scheduler_enabled=True)
    assert not state.exists()


@pytest.mark.unit
def test_fleet_watchdog_surfaces_board_pause_expired() -> None:
    from staff import fleet as fleet_mod

    now = datetime.now(UTC)
    machines = {
        "hub-a": {
            "status": "online",
            "scheduler": {
                "enabled": False,
                "running": False,
                "roles": [],
                "pause": {
                    "applies": True,
                    "pause_until": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                    "expired": True,
                    "source": "env",
                },
            },
        }
    }
    alerts = fleet_mod.scheduler_watchdog_alerts(machines, now=now)
    assert any(a.get("key") == "scheduler-pause-expired:hub-a" for a in alerts)
