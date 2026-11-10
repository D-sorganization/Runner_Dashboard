"""WP16 (#4271): scheduler/retry hardening regression tests."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from staff.holds import Hold, HoldsList
from staff.retry import handle_post_execution_retry
from staff.retry_admission import automatic_retry_admission
from staff.retry_policy import compute_backoff, next_fallback_provider
from staff import roles as roles_mod
from staff.roles import RoleSpec
from staff.runner import RunRequest
from staff.scheduler import StaffScheduler
from staff.store import RunRecord, RunStore
from staff.watchdog import StaffWatchdog

LA = ZoneInfo("America/Los_Angeles")


class _FakeRunner:
    machine = "TestNode"

    def __init__(self, store: RunStore, roles: dict[str, RoleSpec]) -> None:
        self.store = store
        self._roles = roles
        self.submitted: list[RunRequest] = []
        self._adapters = {"codex": MagicMock(), "claude": MagicMock()}
        self._cancel_flags: set[str] = set()

    def roles(self) -> dict[str, RoleSpec]:
        return self._roles

    def submit(self, req: RunRequest) -> RunRecord:
        self.submitted.append(req)
        rec = RunRecord(
            id=f"run-{len(self.submitted)}",
            role=req.role,
            provider="fake",
            model=None,
            machine=self.machine,
            repo=req.repo,
            target_kind="issue" if req.issue else "prompt",
            target_ref=f"#{req.issue}" if req.issue else "",
            prompt=req.prompt,
            requested_by=req.requested_by,
        )
        return rec


@pytest.mark.unit
def test_next_fallback_provider_skips_visited_ping_pong() -> None:
    role = RoleSpec(name="r", title="R", providers=("codex", "claude"), fallback_providers=("codex", "claude"))
    assert next_fallback_provider(role, "codex", visited=frozenset({"claude"})) is None
    assert next_fallback_provider(role, "claude", visited=frozenset()) == "codex"


@pytest.mark.unit
def test_fallback_chain_cap_stops_legacy_scheduling(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "r.sqlite3")
    role = RoleSpec(
        name="r",
        title="R",
        providers=("codex", "claude"),
        fallback_providers=("codex", "claude"),
        max_attempts=2,
    )
    runner = MagicMock()
    runner.machine = "test"
    runner.store = store
    runner._adapters = {"codex": MagicMock(), "claude": MagicMock()}
    runner._cancel_flags = set()
    runner.roles.return_value = {"r": role}

    root = RunRecord(
        id="root",
        role="r",
        provider="codex",
        model="m",
        machine="test",
        repo="org/repo",
        target_kind="prompt",
        target_ref="",
        prompt="x",
        status="failed",
        failure_class="rate_limited",
        attempt=2,
        max_attempts=2,
        provider_chain=json.dumps(
            [
                {"provider": "codex", "outcome": "failed"},
                {"provider": "claude", "outcome": "failed"},
                {"provider": "codex", "outcome": "failed"},
            ]
        ),
    )
    store.create_run(root)
    store.update_run(root.id, status="failed")

    plan = MagicMock()
    plan.role = "r"
    plan.provider = "codex"
    plan.model = "m"
    plan.prompt = "x"
    plan.repo = "org/repo"
    plan.target_kind = "prompt"
    plan.target_ref = ""
    plan.operator_prompt = ""
    plan.branch = "staff/x"
    plan.lease_ritual = False
    plan.consolidation = None
    plan.focus = ""
    plan.head_ref = ""

    with (
        patch("staff.retry.automatic_retry_admission", return_value=(True, "")),
        patch("staff.failover_dispatch.try_chain_failover", return_value=False),
        patch("staff.provider_fallback.try_declarative_fallback", return_value=False),
        patch("staff.retry._schedule") as schedule,
    ):
        assert handle_post_execution_retry(runner, root, plan) is False
        schedule.assert_not_called()


@pytest.mark.unit
def test_automatic_retry_admission_respects_hold(tmp_path: Path) -> None:
    holds_path = tmp_path / "staff_holds.json"
    holds = HoldsList(holds_path, roles_loader=dict)
    holds.replace(
        [
            {
                "id": "h1",
                "text": "freeze dispatch",
                "applies_to": ["*"],
                "active": True,
                "kind": "schedule",
            }
        ]
    )
    store = RunStore(tmp_path / "s.sqlite3")
    runner = MagicMock()
    runner.store = store
    runner.roles.return_value = {"r": RoleSpec(name="r", title="R")}
    rec = RunRecord(
        id="run-1",
        role="r",
        provider="codex",
        model=None,
        machine="test",
        repo="org/repo",
        target_kind="issue",
        target_ref="#99",
        prompt="fix",
        status="failed",
    )
    allowed, reason = automatic_retry_admission(runner, rec, holds=holds)
    assert allowed is False
    assert reason.startswith("hold:")


@pytest.mark.unit
def test_holds_concurrent_upserts_keep_both(tmp_path: Path) -> None:
    path = tmp_path / "staff_holds.json"
    hl = HoldsList(path, roles_loader=dict)
    errors: list[Exception] = []

    def add(text: str) -> None:
        try:
            hl.upsert_hold(
                Hold(
                    id=f"id-{text}",
                    text=text,
                    applies_to=["*"],
                    active=True,
                    kind="schedule",
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=add, args=("hold-a",))
    t2 = threading.Thread(target=add, args=("hold-b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not errors
    texts = {h.text for h in hl.load()}
    assert {"hold-a", "hold-b"}.issubset(texts)


@pytest.mark.unit
def test_scheduler_slot_fire_isolates_exceptions(tmp_path: Path) -> None:
    role = roles_mod.parse_role(
        {
            "name": "role-a",
            "providers": ["fake"],
            "schedule": "0 * * * *",
            "repos": ["Runner_Dashboard"],
        }
    )
    store = RunStore(tmp_path / "sched.sqlite3")
    runner = _FakeRunner(store, {"role-a": role})
    runner._roles = {
        "role-a": role,
        "role-b": roles_mod.parse_role(
            {
                "name": "role-b",
                "providers": ["fake"],
                "schedule": "0 * * * *",
                "repos": ["Runner_Dashboard"],
            }
        ),
    }
    state_file = tmp_path / "staff_schedule_state.json"
    due = datetime(2026, 1, 1, 12, 0, tzinfo=LA)
    sched = StaffScheduler(
        runner,  # type: ignore[arg-type]
        HoldsList(tmp_path / "holds.json", roles_loader=dict),
        MagicMock(can_run=lambda *_a: (True, "")),
        clock=lambda: due,
        state_file=state_file,
    )
    sched.acquire_leader_lease("TestNode", now=due, ttl_seconds=3600)

    calls = {"n": 0}

    def submit(req: RunRequest) -> RunRecord:
        calls["n"] += 1
        if req.role == "role-a":
            raise ValueError("boom")
        rec = RunRecord(
            id=f"run-{len(runner.submitted)}",
            role=req.role,
            provider="fake",
            model=None,
            machine=runner.machine,
            repo=req.repo,
            target_kind="issue" if req.issue else "prompt",
            target_ref=f"#{req.issue}" if req.issue else "",
            prompt=req.prompt,
            requested_by=req.requested_by,
        )
        runner.submitted.append(req)
        return rec

    runner.submit = submit  # type: ignore[method-assign]

    boom = {"role-a": True}

    def fire_slot(now: datetime, **kwargs: object) -> dict[str, object]:
        name = str(kwargs.get("name"))
        if boom.pop(name, False):
            raise RuntimeError("slot boom")
        return {"role": name, "slot": kwargs.get("due"), "fired": True, "run_id": f"run-{name}"}

    with patch.object(sched, "_fire_pending_slot", side_effect=fire_slot):
        pending = [
            {"name": "role-a", "role": role, "due": due.isoformat(), "repo": "Runner_Dashboard", "issue_number": None},
            {
                "name": "role-b",
                "role": runner._roles["role-b"],
                "due": due.isoformat(),
                "repo": "Runner_Dashboard",
                "issue_number": None,
            },
        ]
        decisions: list[dict[str, object]] = []
        for slot in pending:
            try:
                decisions.append(sched._fire_pending_slot(due, **slot))  # noqa: SLF001
            except Exception:  # noqa: BLE001
                decisions.append({"role": slot["name"], "fired": False, "reason": "slot_fire_error"})
    assert any(d.get("reason") == "slot_fire_error" for d in decisions)
    assert any(d.get("fired") for d in decisions)


@pytest.mark.unit
def test_scheduler_submit_runs_without_scheduler_lock(tmp_path: Path) -> None:
    role = roles_mod.parse_role(
        {
            "name": "night-watch",
            "providers": ["fake"],
            "schedule": "0 22 * * *",
            "repos": ["Runner_Dashboard"],
        }
    )
    store = RunStore(tmp_path / "sched2.sqlite3")
    runner = _FakeRunner(store, {"night-watch": role})
    sched = StaffScheduler(
        runner,  # type: ignore[arg-type]
        HoldsList(tmp_path / "holds.json", roles_loader=dict),
        MagicMock(can_run=lambda *_a: (True, "")),
        state_file=tmp_path / "state.json",
    )
    captured: list[bool] = []

    base_submit = _FakeRunner.submit.__get__(runner, _FakeRunner)

    def submit(req: RunRequest) -> RunRecord:
        captured.append(sched._lock._is_owned())  # type: ignore[attr-defined]
        return base_submit(req)

    runner.submit = submit  # type: ignore[method-assign]
    due = datetime(2026, 1, 1, 22, 0, tzinfo=LA)
    with patch.object(sched, "_decider", return_value=None), patch(
        "staff.scheduler.claim_guard.scheduler_blocker",
        return_value="",
    ):
        sched._fire_pending_slot(  # noqa: SLF001
            due,
            name="night-watch",
            role=role,
            due=due.isoformat(),
            repo="Runner_Dashboard",
            issue_number=None,
        )
    assert captured == [False]


@pytest.mark.unit
def test_watchdog_waits_for_orphan_then_expires(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "wd.sqlite3")
    rec = RunRecord(
        id="run-wd",
        role="r",
        provider="p",
        model=None,
        machine="m",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="x",
        status="running",
    )
    store.create_run(rec)

    parent = subprocess.Popen(
        [sys.executable, "-c", "import subprocess,sys,time;c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)']);time.sleep(0.2)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.1)
    wd = StaffWatchdog(
        run_id=rec.id,
        proc=parent,
        store=store,
        max_seconds=30.0,
        idle_seconds=30.0,
        orphan_grace_seconds=0.3,
        check_interval=0.05,
    )
    wd.start()
    try:
        parent.wait(timeout=2.0)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and wd.failure_class is None:
            time.sleep(0.05)
        assert wd.failure_class == "stalled"
    finally:
        wd.stop()
        terminate = __import__("staff.watchdog", fromlist=["terminate_process_group"]).terminate_process_group
        terminate(parent.pid, grace_period=1.0)


def test_compute_backoff_positive() -> None:
    assert compute_backoff(2) > 0
