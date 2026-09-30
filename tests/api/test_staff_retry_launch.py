"""Follow-up retries run on a worker, once (BR-03, Issue #1797).

The follow-up engine used to write a ``queued`` retry row that nothing launched. The
scheduler counted that row as active work, so the role could stay blocked by a
phantom retry. ``staff.retry.launch_retry`` now claims a deterministic attempt id
and starts the worker. The class, attempt, budget and hold gates still apply, and
the attempt keeps the original's thread, work-item and origin links.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from staff import runner as runner_mod
from staff.budget import BudgetGuard
from staff.holds import HoldsList
from staff.retry import RetryDecision, attempt_run_id, launch_retry
from staff.store import RunRecord

from tests.api.test_staff_fleet import staff  # noqa: F401  (fake-CLI runner fixture)


def _failed(staff: runner_mod.StaffRunner, **overrides: Any) -> RunRecord:  # noqa: F811
    rec = RunRecord(
        **{
            "id": "run-orig",
            "role": "ad-hoc",
            "provider": "claude",
            "model": None,
            "machine": "Desk",
            "repo": "",
            "target_kind": "prompt",
            "target_ref": "",
            "prompt": "say ok",
            "status": "failed",
            "failure_class": "provider_error",
            "attempt": 1,
            "max_attempts": 2,
            "thread_id": "th_barb",
            "work_item_id": "wi_1",
            "origin_node": "OGLaptop",
            **overrides,
        }
    )
    return staff.store.create_run(rec)


@pytest.fixture
def holds(tmp_path: Path) -> HoldsList:
    h = HoldsList(tmp_path / "retry-holds.json", roles_loader=dict)
    h.replace([])
    return h


def _wait_terminal(staff: runner_mod.StaffRunner, run_id: str) -> RunRecord:  # noqa: F811
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        rec = staff.store.get_run(run_id)
        if rec is not None and rec.status in ("succeeded", "failed", "cancelled", "blocked"):
            return rec
        time.sleep(0.1)
    raise AssertionError(f"{run_id} never reached a terminal state")


@pytest.mark.unit
def test_a_follow_up_retry_invokes_the_provider_and_keeps_its_links(
    staff: runner_mod.StaffRunner,  # noqa: F811
    holds: HoldsList,
    tmp_path: Path,
) -> None:
    # The fixture's fake provider, made to report a result so the attempt can succeed.
    result = '{"type": "result", "result": "STAFF_RESULT: retried"}'
    (tmp_path / "fake_cli.py").write_text(f"print({result!r})\n", encoding="utf-8")
    original = _failed(staff)

    decision = launch_retry(staff, original, source="followup", holds=holds)

    assert decision == RetryDecision(True, run_id="run-orig-a2", reason="retry launched")
    done = _wait_terminal(staff, "run-orig-a2")
    assert done.status == "succeeded", done.error
    assert (done.thread_id, done.work_item_id, done.origin_node) == ("th_barb", "wi_1", "OGLaptop")
    assert (done.retry_of, done.attempt) == ("run-orig", 2)


@pytest.mark.unit
def test_concurrent_sweeps_launch_exactly_one_attempt(
    staff: runner_mod.StaffRunner,  # noqa: F811
    holds: HoldsList,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _failed(staff)
    launched: list[str] = []
    monkeypatch.setattr(staff, "launch", lambda rec, plan: launched.append(rec.id))
    barrier = threading.Barrier(6)
    decisions: list[RetryDecision] = []

    def sweep() -> None:
        barrier.wait()
        decisions.append(launch_retry(staff, original, source="followup", holds=holds))

    threads = [threading.Thread(target=sweep) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert launched == ["run-orig-a2"]
    assert sorted(d.reason for d in decisions) == ["already claimed"] * 5 + ["retry launched"]


@pytest.mark.unit
def test_an_attempt_already_claimed_elsewhere_is_not_launched_again(
    staff: runner_mod.StaffRunner,  # noqa: F811
    holds: HoldsList,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _failed(staff)
    _failed(staff, id=attempt_run_id("run-orig", 2), status="running", retry_of="run-orig", attempt=2)
    monkeypatch.setattr(staff, "launch", lambda rec, plan: pytest.fail("launched a second attempt"))

    decision = launch_retry(staff, original, source="followup", holds=holds)

    assert decision == RetryDecision(False, run_id="run-orig-a2", reason="already claimed")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "reason_part"),
    [
        ({"failure_class": "needs_input"}, "not retryable"),
        ({"failure_class": "auth_expired"}, "not retryable"),
        ({"attempt": 2}, "retries exhausted"),
        ({"status": "succeeded"}, "is succeeded"),
    ],
    ids=["needs-input", "auth-expired", "exhausted", "not-failed"],
)
def test_refusals_are_enforced(
    staff: runner_mod.StaffRunner,  # noqa: F811
    holds: HoldsList,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    reason_part: str,
) -> None:
    original = _failed(staff, **overrides)
    monkeypatch.setattr(staff, "launch", lambda rec, plan: pytest.fail("launched a refused retry"))

    decision = launch_retry(staff, original, source="followup", holds=holds, allow_classes=frozenset({"stalled"}))

    assert not decision.launched and reason_part in decision.reason
    assert staff.store.get_run("run-orig-a2") is None


@pytest.mark.unit
def test_a_schedule_hold_or_a_spent_budget_refuses(
    staff: runner_mod.StaffRunner,  # noqa: F811
    holds: HoldsList,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _failed(staff)
    monkeypatch.setattr(staff, "launch", lambda rec, plan: pytest.fail("launched a refused retry"))

    holds.replace([{"id": "h1", "text": "freeze ad-hoc", "applies_to": ["ad-hoc"]}])
    held = launch_retry(staff, original, source="followup", holds=holds)
    holds.replace([])
    monkeypatch.setattr(BudgetGuard, "can_run", lambda self, role: (False, "spent"))
    broke = launch_retry(staff, original, source="followup", holds=holds)

    assert held.reason == "hold: freeze ad-hoc"
    assert broke.reason == "daily budget reached: spent"


@pytest.mark.unit
def test_a_stalled_class_is_retryable_only_when_the_caller_allows_it(
    staff: runner_mod.StaffRunner,  # noqa: F811
    holds: HoldsList,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _failed(staff, failure_class="stalled")
    monkeypatch.setattr(staff, "launch", lambda rec, plan: None)

    assert not launch_retry(staff, original, source="post-execution", holds=holds).launched
    assert launch_retry(staff, original, source="followup", holds=holds, allow_classes=frozenset({"stalled"})).launched
