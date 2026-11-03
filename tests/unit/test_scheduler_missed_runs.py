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


def _collect_alerts(mp: pytest.MonkeyPatch) -> list[dict]:
    alerts: list[dict] = []
    mp.setattr(smr, "emit_missed_run_alert", lambda alert, now=None: alerts.append(alert) or True)
    return alerts


@pytest.mark.unit
def test_first_miss_alone_does_not_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    alerts = _collect_alerts(monkeypatch)
    entry: dict = {}
    smr.note_scheduled_miss(entry, role="bob", job=None, slot="s1", machine="hub-a")
    assert entry["consecutive_missed_runs"] == 1
    assert alerts == []


@pytest.mark.unit
def test_debounce_no_duplicate_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """One alert per streak, however long the streak runs (not a re-alert per miss)."""
    alerts = _collect_alerts(monkeypatch)
    entry: dict = {}
    for slot in ("s1", "s2", "s3", "s4"):
        smr.note_scheduled_miss(entry, role="bob", job=None, slot=slot, machine="hub-a")
    assert entry["consecutive_missed_runs"] == 4
    assert [a["count"] for a in alerts] == [2]
    smr.record_success(entry)
    for slot in ("s5", "s6"):
        smr.note_scheduled_miss(entry, role="bob", job=None, slot=slot, machine="hub-a")
    assert [a["count"] for a in alerts] == [2, 2]


class _Run:
    def __init__(self, status: str, verification: str = "") -> None:
        self.status = status
        self.verification = verification


class _Store:
    def __init__(self, runs: dict[str, _Run]) -> None:
        self.runs = runs

    def get_run(self, run_id: str) -> _Run | None:
        return self.runs.get(run_id)


@pytest.mark.unit
def test_firing_does_not_reset_the_streak() -> None:
    entry = {"consecutive_missed_runs": 1}
    smr.record_scheduled_fire(entry, slot="s2", run_id="run-a", fired_at="t", role="bob", job=None)
    assert entry["consecutive_missed_runs"] == 1
    assert entry["pending_run"]["run_id"] == "run-a"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "verification", "expected"),
    [
        ("succeeded", "", 0),
        ("succeeded", "failed", 2),
        ("failed", "", 2),
        ("cancelled", "", 2),
        ("running", "", 1),
    ],
)
def test_scheduled_run_outcome_resets_or_counts(
    monkeypatch: pytest.MonkeyPatch, status: str, verification: str, expected: int
) -> None:
    """A successful run resets the streak; a failed one is a miss; a live one waits."""
    alerts = _collect_alerts(monkeypatch)
    state = {"bob::nightly": {"consecutive_missed_runs": 1}}
    smr.record_scheduled_fire(state["bob::nightly"], slot="s2", run_id="run-a", fired_at="t", role="bob", job="nightly")
    changed = smr.reconcile_scheduled_runs(state, store=_Store({"run-a": _Run(status, verification)}), machine="hub-a")
    entry = state["bob::nightly"]
    assert entry["consecutive_missed_runs"] == expected
    assert changed is (expected != 1)
    assert ("pending_run" in entry) is (expected == 1)
    assert [a["job"] for a in alerts] == (["nightly"] if expected == 2 else [])
