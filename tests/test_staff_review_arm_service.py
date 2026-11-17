"""Service status, dispatch cap and stall alert (Refs #4249)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from staff.review_arm.armer import ArmOutcome
from staff.review_arm.ledger import Ledger
from staff.review_arm.policy import Candidate, LiveHead
from staff.review_arm.processor import VerdictProcessor
from staff.review_arm.service import ReviewArmService
from staff.review_arm.verdicts import RunOutcome

HEAD = "a" * 40


def candidate(pr: int = 42, **kwargs: object) -> Candidate:
    base = Candidate(
        "Runner_Dashboard",
        pr,
        HEAD,
        author_provider="cursor-agent",
        checks_green=True,
        checks_head=HEAD,
        holds_available=True,
    )
    return replace(base, **kwargs)  # type: ignore[arg-type]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_status_reports_host_queue_and_times() -> None:
    port = Mock()
    port.candidates.return_value = [candidate(), candidate(43, approved=True)]
    clock = Clock()
    service = ReviewArmService(port, enabled=True, dry_run=True, host="hub-a", clock=clock)
    service.cycle()
    status = service.status()
    assert status["host"] == "hub-a"
    assert status["dry_run"] is True
    assert status["last_intake"] == clock.now.isoformat()
    assert status["last_dispatch"] == clock.now.isoformat()  # dry-run: would-dispatch
    assert status["last_arm"] == clock.now.isoformat()
    assert status["queue_depth"] == 1  # the review obligation stays open in dry-run
    actions = [d["action"] for d in status["recent_decisions"]]  # type: ignore[union-attr]
    assert actions == ["review", "arm"]


def test_dispatch_cap_defers_extra_reviews() -> None:
    port = Mock()
    port.candidates.return_value = [candidate(n) for n in range(1, 5)]
    port.refresh.side_effect = lambda c: c
    service = ReviewArmService(port, enabled=True, dry_run=False, max_dispatch=2)
    service.cycle()
    assert port.dispatch.call_count == 2
    status = service.status()
    assert status["counts"] == {"review": 2, "dispatch_cap_defer": 2}
    assert status["queue_depth"] == 2


def test_stall_alert_fires_once_per_episode_and_resets_on_progress() -> None:
    port = Mock()
    port.candidates.return_value = [candidate(1)]
    port.refresh.side_effect = lambda c: c
    alerts: list[tuple[str, str]] = []
    clock = Clock()
    service = ReviewArmService(
        port,
        enabled=True,
        dry_run=False,
        max_dispatch=0,
        stall_minutes=30,
        alert=lambda title, text: alerts.append((title, text)),
        clock=clock,
    )
    service.cycle()
    assert alerts == []
    clock.now += timedelta(minutes=31)
    service.cycle()
    assert len(alerts) == 1 and "queue depth 1" in alerts[0][1]
    clock.now += timedelta(minutes=10)
    service.cycle()
    assert len(alerts) == 1  # same episode
    assert service.status()["stalled_since"]
    port.candidates.return_value = []  # queue drained
    service.cycle()
    assert service.status()["stalled_since"] == ""


def test_no_stall_alert_when_queue_empty_or_disabled() -> None:
    port = Mock()
    port.candidates.return_value = []
    alerts: list[object] = []
    clock = Clock()
    service = ReviewArmService(port, enabled=True, stall_minutes=1, alert=lambda *a: alerts.append(a), clock=clock)
    service.cycle()
    clock.now += timedelta(hours=5)
    service.cycle()
    assert alerts == []
    disabled = ReviewArmService(port, enabled=False, stall_minutes=1, alert=lambda *a: alerts.append(a), clock=clock)
    assert disabled.check_stall() is False


def test_failed_dispatch_keeps_obligation_queued() -> None:
    port = Mock()
    port.candidates.return_value = [candidate(1)]
    port.refresh.side_effect = lambda c: c
    port.dispatch.side_effect = RuntimeError("429 budget")
    service = ReviewArmService(port, enabled=True, dry_run=False)
    service.cycle()
    assert service.status()["queue_depth"] == 1
    assert service.status()["last_dispatch"] == ""


class _Port:
    def __init__(self) -> None:
        self.armed: list[str] = []

    def run_outcome(self, run_id: str) -> RunOutcome:
        return RunOutcome("verdict", "approve", 0, False, "", "ok a.py:1")

    def live(self, repo: str, pr: int) -> LiveHead:
        return LiveHead(HEAD, "OPEN", False)

    def arm(self, repo: str, pr: int, head: str, *, holds: frozenset[tuple[str, int]]) -> ArmOutcome:
        self.armed.append(head)
        return ArmOutcome(True, "armed")

    def post_findings(self, *a: object) -> None:
        pass

    def escalate(self, *a: object) -> None:
        pass


def test_verdict_phase_runs_before_intake_and_updates_times(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_dispatch("Tools", 7, HEAD, run_id="run-1", round_no=1, provider="claude")
    vport = _Port()
    port = Mock()
    port.candidates.return_value = []
    clock = Clock()
    processor = VerdictProcessor(ledger, vport, dry_run=False)
    service = ReviewArmService(port, enabled=True, dry_run=False, processor=processor, clock=clock)
    service.cycle()
    assert vport.armed == [HEAD]
    status = service.status()
    assert status["last_verdict"] == clock.now.isoformat()
    assert status["last_arm"] == clock.now.isoformat()
    assert status["counts"] == {"verdict_arm": 1}
