"""Regression tests for hub startup health vs staff scheduler barb slots (#3526)."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from staff import scheduler_startup as startup_mod
from staff.budget import BudgetGuard
from staff.holds import HoldsList
from staff.roles import parse_role
from staff.runner import RunRequest, StaffRunner
from staff.scheduler import StaffScheduler


def _barb_role(**extra: Any) -> Any:
    data = {
        "name": "barb",
        "title": "Barb",
        "providers": ["claude"],
        "schedule": "* * * * *",
        "repos": ["Runner_Dashboard"],
        **extra,
    }
    return parse_role(data)


@pytest.fixture(autouse=True)
def _reset_startup_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    startup_mod._started_at = 0.0
    monkeypatch.setenv("STAFF_SCHEDULER_STARTUP_GRACE_SECONDS", "60")


def test_barb_due_slot_skipped_during_startup_grace(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    fired: list[str] = []

    class FakeRunner:
        machine = "test"
        store = type("S", (), {"active_runs": lambda self: []})()

        def roles(self) -> dict[str, RoleSpec]:
            return {"barb": _barb_role()}

        def submit(self, req: RunRequest) -> Any:
            fired.append(req.role)
            raise AssertionError("should not fire during grace")

    startup_mod.mark_scheduler_started(time.monotonic())
    sched = StaffScheduler(
        FakeRunner(),  # type: ignore[arg-type]
        HoldsList(roles_loader=lambda: {}),
        BudgetGuard(FakeRunner()),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 10, 29, 12, 0, tzinfo=UTC),
        state_file=tmp_path / "state.json",
        decider=lambda *_a, **_k: None,
    )
    monkeypatch.setattr(sched, "_lease_allows_firing", lambda _now: True)
    out = sched.tick()
    assert fired == []
    assert out == []


def test_health_can_read_schedule_while_slow_decider_runs(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()
    release = threading.Event()

    def slow_decider(_role: Any, _repo: str | None) -> None:
        gate.set()
        assert release.wait(5)
        return None

    class FakeRunner:
        machine = "test"

        class _Store:
            def active_runs(self) -> list[Any]:
                return []

            def create_run(self, rec: Any) -> None:
                pass

            def append_event(self, *_a: Any, **_k: Any) -> None:
                pass

        store = _Store()

        def roles(self) -> dict[str, Any]:
            return {
                "maintenance": parse_role(
                    {
                        "name": "maintenance",
                        "title": "M",
                        "providers": ["claude"],
                        "schedule": "* * * * *",
                        "repos": ["Runner_Dashboard"],
                    }
                )
            }

        def submit(self, req: RunRequest) -> Any:
            return type("R", (), {"id": "run-test"})()

    runner = FakeRunner()
    sched = StaffScheduler(
        runner,  # type: ignore[arg-type]
        HoldsList(roles_loader=runner.roles),
        BudgetGuard(runner),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 10, 29, 12, 0, tzinfo=UTC),
        state_file=tmp_path / "state.json",
        decider=slow_decider,
    )
    state_file = tmp_path / "state.json"
    state_file.write_text(
        '{"maintenance": {"cursor": "2026-10-29T11:00:00+00:00"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(sched, "_lease_allows_firing", lambda _now: True)
    monkeypatch.setattr("staff.scheduler.claim_guard.scheduler_blocker", lambda *_a, **_k: "")

    def always_due(_role: Any, now: datetime) -> dict[str, Any]:
        return {
            "role": _role.name,
            "schedule": _role.schedule,
            "window": _role.window,
            "next_fire": now.isoformat(),
            "in_window": True,
            "hold": None,
            "active_run": None,
            "budget_ok": True,
            "budget_reason": "",
            "last_fired": None,
            "last_reason": "",
            "enabled": True,
        }

    monkeypatch.setattr(sched, "evaluate", always_due)

    tick_thread = threading.Thread(target=sched.tick)
    tick_thread.start()
    assert gate.wait(2), "scheduler tick should reach slow decider"
    started = time.perf_counter()
    status = sched.status()
    elapsed = time.perf_counter() - started
    release.set()
    tick_thread.join(timeout=5)
    assert elapsed < 1.0, f"status() blocked {elapsed:.2f}s while decider held work"
    assert status and status[0]["role"] == "maintenance"
