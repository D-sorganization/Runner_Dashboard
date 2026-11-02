"""Missed scheduled-run alerts (#3822)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from staff import scheduler_missed_runs as smr


@pytest.fixture(autouse=True)
def _reset() -> None:
    smr.reset_missed_run_alert_debounce_for_tests()
    yield
    smr.reset_missed_run_alert_debounce_for_tests()


@pytest.mark.unit
def test_note_scheduled_miss_alerts_on_second_consecutive() -> None:
    entry: dict = {}
    smr.note_scheduled_miss(entry, role="bob", job=None, slot="2026-11-02T09:00:00", machine="hub-a")
    assert entry["consecutive_missed_runs"] == 1

    events: list[str] = []

    import push

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("fleet_events.get_event_store", lambda: MagicMock(record=lambda ev: events.append(ev.title)))
        mp.setattr(push, "notify", lambda *a, **k: None)
        smr.note_scheduled_miss(entry, role="bob", job=None, slot="2026-11-02T10:00:00", machine="hub-a")

    assert entry["consecutive_missed_runs"] == 2
    assert len(events) == 1


@pytest.mark.unit
def test_record_success_resets_counter() -> None:
    entry = {"consecutive_missed_runs": 2}
    smr.record_success(entry)
    assert entry["consecutive_missed_runs"] == 0
