"""Regression for #3788 follow-up: ``reconcile_pending`` result-retention gaps.

Independent review of the merged #3795 wiring (comment on #3788) found two counterexamples
against the "a failed/partial run must never be recorded as a successful sweep" acceptance:

1. A run whose ``RunRecord.status`` is ``"succeeded"`` but whose post-run
   ``verification`` is ``"failed"`` (#1516) still reached ``run_cartographer_slot`` and had
   its capsule/report persisted as if the sweep succeeded.
2. ``run_cartographer_slot`` returning a non-``"executed"`` result (``"failed"``, or
   ``"skipped"``/``fallback_lease_held``) was still unconditionally popped from ``pending``
   and logged as "recorded", discarding a retryable failure instead of leaving it for the
   next tick.

These tests drive ``reconcile_pending`` directly (unit-level, not the full scheduler/runner
integration already covered by ``tests/api/test_staff_scheduler_cartographer.py``) with a
fake store and a monkeypatched ``cartographer_persona.run_cartographer_slot``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from staff import cartographer_persona, scheduler_cartographer

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 8, 4, 5, tzinfo=LA)


@dataclass
class _FakeRec:
    status: str
    verification: str = ""
    outcome: str = "Cartographer weekly map sweep"


class _FakeStore:
    def __init__(self, records: dict[str, _FakeRec]) -> None:
        self._records = records

    def get_run(self, run_id: str) -> _FakeRec | None:
        return self._records.get(run_id)


def _reconcile(records: dict[str, _FakeRec], pending: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    lock = threading.RLock()
    scheduler_cartographer.reconcile_pending(
        lock,
        pending,
        store=_FakeStore(records),
        continuity_factory=lambda: None,
        capsules_factory=lambda: None,
        notify_opts={},
        machine="TestNode",
        now=NOW,
    )
    return pending


@pytest.mark.unit
def test_succeeded_run_with_failed_verification_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        cartographer_persona, "run_cartographer_slot", lambda *a, **k: calls.append("called") or {"status": "executed"}
    )
    pending = {"run-1": {"job": "weekly_map_sweep", "slot": "2026-11-08T04:00:00"}}
    records = {"run-1": _FakeRec(status="succeeded", verification="failed")}

    _reconcile(records, pending)

    assert calls == []  # the persona contract must never run for a failed-verification sweep
    assert pending == {}  # dropped, not left pending forever


@pytest.mark.unit
def test_executed_result_pops_pending_and_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cartographer_persona, "run_cartographer_slot", lambda *a, **k: {"status": "executed", "slot_key": "k"}
    )
    pending = {"run-1": {"job": "weekly_map_sweep", "slot": "2026-11-08T04:00:00"}}
    records = {"run-1": _FakeRec(status="succeeded", verification="verified")}

    result = _reconcile(records, pending)

    assert result == {}


@pytest.mark.unit
def test_failed_persona_result_stays_pending_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A capsule-save/notify failure inside the persona contract must remain retryable."""
    monkeypatch.setattr(
        cartographer_persona, "run_cartographer_slot", lambda *a, **k: {"status": "failed", "reason": "boom"}
    )
    info = {"job": "weekly_map_sweep", "slot": "2026-11-08T04:00:00"}
    pending = {"run-1": dict(info)}
    records = {"run-1": _FakeRec(status="succeeded")}

    result = _reconcile(records, pending)

    assert result == {"run-1": info}  # left in place, not silently discarded


@pytest.mark.unit
def test_fallback_lease_held_result_stays_pending_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cartographer_persona,
        "run_cartographer_slot",
        lambda *a, **k: {"status": "skipped", "reason": "fallback_lease_held"},
    )
    info = {"job": "weekly_map_sweep", "slot": "2026-11-08T04:00:00"}
    pending = {"run-1": dict(info)}
    records = {"run-1": _FakeRec(status="succeeded")}

    result = _reconcile(records, pending)

    assert result == {"run-1": info}


@pytest.mark.unit
def test_slot_already_executed_elsewhere_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A duplicate-slot skip is terminal (someone else already delivered it) -- drop it."""
    monkeypatch.setattr(
        cartographer_persona,
        "run_cartographer_slot",
        lambda *a, **k: {"status": "skipped", "reason": "slot_already_executed"},
    )
    pending = {"run-1": {"job": "weekly_map_sweep", "slot": "2026-11-08T04:00:00"}}
    records = {"run-1": _FakeRec(status="succeeded")}

    result = _reconcile(records, pending)

    assert result == {}


@pytest.mark.unit
def test_non_terminal_run_is_left_untouched() -> None:
    info = {"job": "weekly_map_sweep", "slot": "2026-11-08T04:00:00"}
    pending = {"run-1": dict(info)}
    records = {"run-1": _FakeRec(status="running")}

    result = _reconcile(records, pending)

    assert result == {"run-1": info}
