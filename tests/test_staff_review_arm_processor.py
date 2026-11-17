"""Verdict processing: arm gate, blocked heads, escalation, relay, dry-run (Refs #4249)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from staff.review_arm.armer import ArmOutcome
from staff.review_arm.ledger import Ledger
from staff.review_arm.policy import LiveHead
from staff.review_arm.processor import VerdictProcessor
from staff.review_arm.verdicts import RunOutcome, extract_outcome

HEAD = "a" * 40
NEW = "b" * 40
APPROVE = RunOutcome("verdict", "approve", 0, False, HEAD[:7], "Checked a.py:3; tests pass.")


@dataclass
class FakePort:
    outcomes: dict[str, RunOutcome] = field(default_factory=dict)
    live_head: LiveHead = field(default_factory=lambda: LiveHead(HEAD, "OPEN", False))
    armed: list[tuple[str, int, str]] = field(default_factory=list)
    posted: list[tuple[str, int, str]] = field(default_factory=list)
    escalated: list[tuple[str, int, str]] = field(default_factory=list)

    def run_outcome(self, run_id: str) -> RunOutcome:
        return self.outcomes[run_id]

    def live(self, repo: str, pr: int) -> LiveHead:
        return self.live_head

    def arm(self, repo: str, pr: int, head: str, *, holds: frozenset[tuple[str, int]]) -> ArmOutcome:
        self.armed.append((repo, pr, head))
        return ArmOutcome(True, "armed")

    def post_findings(self, repo: str, pr: int, head: str, run_id: str, text: str) -> None:
        self.posted.append((repo, pr, run_id))

    def escalate(self, repo: str, pr: int, head: str, reason: str, detail: str) -> None:
        self.escalated.append((repo, pr, reason))

    @property
    def writes(self) -> list[Any]:
        return [*self.armed, *self.posted, *self.escalated]


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.sqlite")


def _dispatch(ledger: Ledger, run_id: str = "run-1", *, repo: str = "Tools", round_no: int = 1) -> None:
    ledger.record_dispatch(repo, 7, HEAD, run_id=run_id, round_no=round_no, provider="claude")


def _run(ledger: Ledger, port: FakePort, *, dry_run: bool = False) -> list[Any]:
    proc = VerdictProcessor(ledger, port, dry_run=dry_run)
    return [proc.process(d) for d in proc.pending()]


def test_approve_at_same_head_arms_once(ledger: Ledger) -> None:
    _dispatch(ledger)
    port = FakePort(outcomes={"run-1": APPROVE})
    [result] = _run(ledger, port)
    assert result.decision.action == "arm" and result.armed
    assert port.armed == [("Tools", 7, HEAD)]
    assert port.posted == [("Tools", 7, "run-1")]
    assert _run(ledger, port) == []  # handled exactly once
    assert port.armed == [("Tools", 7, HEAD)]


def test_pending_run_is_left_for_next_cycle(ledger: Ledger) -> None:
    _dispatch(ledger)
    port = FakePort(outcomes={"run-1": RunOutcome("pending")})
    assert _run(ledger, port) == [None]
    assert len(ledger.pending_dispatches()) == 1


def test_moved_head_does_not_arm(ledger: Ledger) -> None:
    _dispatch(ledger)
    port = FakePort(outcomes={"run-1": APPROVE}, live_head=LiveHead(NEW, "OPEN", False))
    [result] = _run(ledger, port)
    assert (result.decision.action, result.decision.reason) == ("requeue", "head_moved")
    assert port.armed == []


def test_hold_label_does_not_arm(ledger: Ledger) -> None:
    _dispatch(ledger)
    port = FakePort(outcomes={"run-1": APPROVE}, live_head=LiveHead(HEAD, "OPEN", False, ("on-hold",)))
    [result] = _run(ledger, port)
    assert result.decision.reason == "hold_label"
    assert port.armed == []


def test_imported_hold_does_not_arm(ledger: Ledger) -> None:
    ledger.import_box_state(holds="Tools 7 # standing\n", blocked="", overrides="")
    _dispatch(ledger)
    port = FakePort(outcomes={"run-1": APPROVE})
    assert _run(ledger, port)[0].decision.reason == "held_pr"


def test_blocked_head_is_never_armed_by_a_later_approve(ledger: Ledger) -> None:
    _dispatch(ledger, "run-1")
    _dispatch(ledger, "run-2")
    changes = RunOutcome("verdict", "changes", 1, False, "", "- [BLOCKING] bug a.py:1")
    port = FakePort(outcomes={"run-1": changes, "run-2": APPROVE})
    first, second = _run(ledger, port)
    assert first.decision.reason == "blocking_verdict"
    assert second.decision.reason == "blocked_head"
    assert port.armed == []


def test_imported_blocked_head_and_override(ledger: Ledger) -> None:
    ledger.import_box_state(holds="", blocked=f"Tools 7 {HEAD[:8]} seed\n", overrides="")
    _dispatch(ledger)
    port = FakePort(outcomes={"run-1": APPROVE})
    assert _run(ledger, port)[0].decision.reason == "blocked_head"
    ledger.override("Tools", 7, HEAD, operator="Dieter", reason="false positive")
    _dispatch(ledger, "run-2")
    port.outcomes["run-2"] = APPROVE
    assert _run(ledger, port)[0].decision.action == "arm"


def test_zero_blocking_changes_is_policy2_approve(ledger: Ledger) -> None:
    _dispatch(ledger)
    out = RunOutcome("verdict", "changes", 0, False, "", "- [NONBLOCKING] nit a.py:2")
    port = FakePort(outcomes={"run-1": out})
    assert _run(ledger, port)[0].decision.action == "arm"


def test_round_two_still_blocking_escalates_to_owner_once(ledger: Ledger) -> None:
    _dispatch(ledger, "run-1", round_no=2)
    changes = RunOutcome("verdict", "changes", 2, False, "", "- [BLOCKING] a\n- [BLOCKING] b")
    port = FakePort(outcomes={"run-1": changes, "run-2": changes})
    assert _run(ledger, port)[0].decision.reason == "round_still_blocking"
    _dispatch(ledger, "run-2", round_no=3)
    _run(ledger, port)
    assert port.escalated == [("Tools", 7, "round_still_blocking")]


def test_hub_restart_recovers_verdict_from_events_and_arms(ledger: Ledger) -> None:
    """The run failed in its post-run step (e.g. hub restart); the verdict is in its events."""
    _dispatch(ledger)
    events = [{"kind": "result", "text": f"Looks right a.py:4\nSTAFF_RESULT: review approve #7 @{HEAD[:7]}"}]
    port = FakePort(outcomes={"run-1": extract_outcome("failed", "", events)})
    restarted = Ledger(ledger.path)  # a fresh process reads the same durable ledger
    [result] = _run(restarted, port)
    assert result.decision.action == "arm"
    assert port.armed == [("Tools", 7, HEAD)]


def test_failed_run_without_verdict_is_handled_for_retry(ledger: Ledger) -> None:
    _dispatch(ledger)
    port = FakePort(outcomes={"run-1": RunOutcome("failed_no_verdict")})
    [result] = _run(ledger, port)
    assert (result.decision.action, result.decision.reason) == ("requeue", "failed_no_verdict")
    assert port.writes == []


def test_verdict_for_another_head_is_not_trusted(ledger: Ledger) -> None:
    _dispatch(ledger)
    out = RunOutcome("verdict", "approve", 0, False, NEW[:7], "ok a.py:1")
    port = FakePort(outcomes={"run-1": out})
    assert _run(ledger, port)[0].decision.reason == "verdict_head_mismatch"
    assert port.armed == []


def test_rubber_stamp_escalates_instead_of_arming(ledger: Ledger) -> None:
    _dispatch(ledger)
    port = FakePort(outcomes={"run-1": RunOutcome("verdict", "approve", 0, False, "", "")})
    assert _run(ledger, port)[0].decision.reason == "rubber_stamp"
    assert port.armed == []


def test_work_private_findings_are_never_posted(ledger: Ledger) -> None:
    _dispatch(ledger, repo="work_private")
    port = FakePort(outcomes={"run-1": APPROVE})
    _run(ledger, port)
    assert port.posted == []


def test_dry_run_makes_no_writes_and_escalates_later_when_live(ledger: Ledger) -> None:
    _dispatch(ledger, "run-1")
    _dispatch(ledger, "run-2", round_no=2)
    changes = RunOutcome("verdict", "changes", 1, False, "", "- [BLOCKING] a")
    port = FakePort(outcomes={"run-1": APPROVE, "run-2": changes})
    results = _run(ledger, port, dry_run=True)
    assert [r.decision.action for r in results] == ["arm", "escalate"]
    assert port.writes == []
    assert ledger.escalations() == []
