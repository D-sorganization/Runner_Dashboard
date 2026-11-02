"""Regression test for issue #3788.

``StaffScheduler._fire_pending_slot`` used to submit a due Cartographer slot as a bare
generic ``RunRequest`` via ``runner.submit(req)`` -- it never touched
``cartographer_persona.run_cartographer_slot`` or the persona fallback-lease/slot-ledger/
capsule contract. A scheduled Cartographer slot firing in production therefore never
produced the evidence-linked report the persona contract promises, and an active Grok
fallback lease could not prevent RD from also invoking the model for the same slot.

This drives a real ``StaffScheduler.tick()`` through a real ``StaffRunner`` (fake-CLI
subprocess as the only external boundary, same pattern as ``test_staff_consolidation.py``)
to a terminal run status, then asserts on the *real* persona contract: the
``persona_slot_ledger`` row and the saved ``RoleCapsuleStore`` capsule -- not a mock of the
fix itself.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from staff import adapters as adapters_mod
from staff import budget as budget_mod
from staff import holds as holds_mod
from staff import persona_fallback_lease, runner_registry
from staff import runner as runner_mod
from staff import scheduler as scheduler_mod
from staff import store as store_mod
from staff import workspace as workspace_mod
from staff.barb_continuity import BarbContinuityStore
from staff.role_capsule import RoleCapsuleStore

LA = ZoneInfo("America/Los_Angeles")

_FAKE_CLI = """
import json
usage = {"input_tokens": 10, "output_tokens": 5}
result = "STAFF_RESULT: mapped 6 repos, 0 stale"
print(json.dumps({"type": "result", "result": result, "usage": usage, "total_cost_usd": 0.01}))
"""


@pytest.fixture
def role_dir(tmp_path: Path) -> Path:
    d = tmp_path / "roles"
    d.mkdir()
    (d / "cartographer.yml").write_text(
        "\n".join(
            [
                "name: cartographer",
                "title: The Silent Cartographer",
                "summary: Keeps codebase maps accurate.",
                "playbook: docs/fleet-cartographer.md",
                "prompt_template: null",
                "instructions: Keeps codebase maps accurate.",
                "providers: [fake]",
                "model: default",
                "schedule: '0 4 * * 0'",
                "window: {start: '22:00', end: '06:00'}",
                "repos: [UpstreamDrift]",
                "scope: {}",
                "budget: {usd_per_run: 3, usd_per_day: 15}",
                (
                    "permissions: {lease: true, push_branch: true, open_pr: true, "
                    "merge: false, host_shell: false, notify_user: false}"
                ),
                "reports_to: barb",
                "holds: []",
                "surface: dashboard",
            ]
        ),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def staff(tmp_path: Path, role_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[runner_mod.StaffRunner]:
    script = tmp_path / "fake_cli.py"
    script.write_text(_FAKE_CLI, encoding="utf-8")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(role_dir))
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(tmp_path / "wt"))
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path / "nonexistent-repos"))
    fake = adapters_mod.ProviderAdapter(
        provider_id="fake", label="Fake", executable=sys.executable, argv=(str(script), "{prompt}"), json_lines=True
    )
    adapters = {**adapters_mod.ADAPTERS, "fake": fake}
    monkeypatch.setattr(adapters_mod, "ADAPTERS", adapters)
    # Repo-scoped runs would clone and add a git worktree; stub both so the test stays offline.
    monkeypatch.setattr(workspace_mod, "find_repo_checkout", lambda _repo: tmp_path / "checkout")
    monkeypatch.setattr(workspace_mod, "add_worktree", lambda _c, wt, _b: wt.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(
        runner_mod.ops,
        "ensure_worktree_bootstrap_ready",
        lambda *_a, **_k: runner_mod.ops.WorktreeBootstrapPreflight(ok=True),
    )
    store_mod.reset_store()
    runner_mod.reset_runner()
    r = runner_mod.StaffRunner(
        store=store_mod.RunStore(tmp_path / "runs.sqlite3"), adapters=adapters, machine="TestNode"
    )
    monkeypatch.setattr(runner_registry, "_runner", r)
    yield r
    store_mod.reset_store()
    runner_mod.reset_runner()


def _wait_terminal(store: store_mod.RunStore, run_id: str, timeout: float = 20.0) -> store_mod.RunRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = store.get_run(run_id)
        assert rec is not None
        if rec.status not in store_mod.ACTIVE_STATUSES:
            return rec
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still active")


def _scheduler(
    staff: runner_mod.StaffRunner,
    tmp_path: Path,
    clock: dict[str, datetime],
    *,
    continuity: BarbContinuityStore,
    capsules: RoleCapsuleStore,
    notify_opts: dict[str, Any],
) -> scheduler_mod.StaffScheduler:
    holds = holds_mod.HoldsList(tmp_path / "holds.json", roles_loader=dict)
    holds.replace([])
    guard = budget_mod.BudgetGuard(staff.store, alert_sink=lambda *_: None, clock=lambda: clock["now"])
    return scheduler_mod.StaffScheduler(
        staff,
        holds,
        guard,
        clock=lambda: clock["now"],
        state_file=tmp_path / "state.json",
        cartographer_continuity_store=continuity,
        cartographer_capsule_store=capsules,
        cartographer_notify_opts=notify_opts,
    )


@pytest.mark.unit
def test_tick_fires_cartographer_through_persona_fallback_and_capsule_contract(
    staff: runner_mod.StaffRunner, tmp_path: Path
) -> None:
    continuity = BarbContinuityStore(tmp_path / "continuity.db")
    capsules = RoleCapsuleStore(tmp_path / "capsules")
    sent: list[dict[str, Any]] = []
    notify_opts = {
        "config": {"RD_BARB_WEBHOOK_URL": "https://hook.example/cartographer", "RD_BARB_WEBHOOK_KEY": "k"},
        "state_path": tmp_path / "cartographer-notify.json",
        "sender": lambda _url, _hdrs, msg: sent.append(msg),
    }
    clock = {"now": datetime(2026, 11, 8, 3, 59, tzinfo=LA)}
    sched = _scheduler(staff, tmp_path, clock, continuity=continuity, capsules=capsules, notify_opts=notify_opts)

    assert sched.tick() == []  # cursor seeded; the 04:00 Sunday slot is not yet due

    clock["now"] = datetime(2026, 11, 8, 4, 0, 30, tzinfo=LA)
    decisions = sched.tick()
    assert len(decisions) == 1 and decisions[0]["fired"] is True
    rec = _wait_terminal(staff.store, decisions[0]["run_id"])
    assert rec.status == "succeeded"

    # A later tick reconciles the now-terminal run through the persona contract.
    sched.tick()

    ledger = persona_fallback_lease.list_slot_ledger(continuity, role="cartographer")
    assert len(ledger) == 1
    assert ledger[0]["job"] == "weekly_map_sweep"

    capsule = capsules.load_capsule("cartographer")
    assert capsule is not None
    assert capsule["last_scorecard_date"] == "2026-11-08"

    # The "update"-kind notify envelope is queued for Barb's digest, not sent synchronously --
    # the capsule save above is "report generated" evidence; this is the separate transport-delivery
    # record. Neither one is the consumer (Barb) acknowledgment, which is a later, distinct step.
    assert sent == []
    notify_state = json.loads((tmp_path / "cartographer-notify.json").read_text(encoding="utf-8"))
    assert len(notify_state["digest"]) == 1
    assert "Cartographer" in notify_state["digest"][0]["summary"]


@pytest.mark.unit
def test_tick_never_records_a_capsule_for_a_failed_cartographer_run(
    staff: runner_mod.StaffRunner, tmp_path: Path
) -> None:
    """Acceptance #3788: a failed/partial run must never be recorded as a successful sweep."""
    (tmp_path / "fake_cli.py").write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    continuity = BarbContinuityStore(tmp_path / "continuity.db")
    capsules = RoleCapsuleStore(tmp_path / "capsules")
    clock = {"now": datetime(2026, 11, 8, 3, 59, tzinfo=LA)}
    sched = _scheduler(
        staff, tmp_path, clock, continuity=continuity, capsules=capsules, notify_opts={"sender": lambda *_a: None}
    )
    assert sched.tick() == []  # cursor seeded; the 04:00 Sunday slot is not yet due

    clock["now"] = datetime(2026, 11, 8, 4, 0, 30, tzinfo=LA)
    decisions = sched.tick()
    assert len(decisions) == 1 and decisions[0]["fired"] is True
    rec = _wait_terminal(staff.store, decisions[0]["run_id"])
    assert rec.status == "failed"

    sched.tick()
    assert persona_fallback_lease.list_slot_ledger(continuity, role="cartographer") == []
    assert capsules.load_capsule("cartographer") is None


@pytest.mark.unit
def test_tick_skips_cartographer_firing_while_a_fallback_lease_is_held(
    staff: runner_mod.StaffRunner, tmp_path: Path
) -> None:
    """Acceptance #3788: an active fallback lease must prevent duplicate/overlapping model invocation."""
    continuity = BarbContinuityStore(tmp_path / "continuity.db")
    capsules = RoleCapsuleStore(tmp_path / "capsules")
    clock = {"now": datetime(2026, 11, 8, 3, 59, tzinfo=LA)}
    sched = _scheduler(
        staff, tmp_path, clock, continuity=continuity, capsules=capsules, notify_opts={"sender": lambda *_a: None}
    )
    assert sched.tick() == []  # cursor seeded; the 04:00 Sunday slot is not yet due

    clock["now"] = datetime(2026, 11, 8, 4, 0, 30, tzinfo=LA)
    persona_fallback_lease.acquire_persona_lease(
        continuity, "cartographer", holder=persona_fallback_lease.GROK_FALLBACK_HOLDER, now=clock["now"]
    )
    decisions = sched.tick()
    assert len(decisions) == 1
    assert decisions[0]["fired"] is False
    assert decisions[0]["reason"] == "fallback_lease_held"
    assert staff.store.active_runs() == []
