"""Box-state import, verdict recovery and the armd-parity arm gate (Refs #4249)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.review_arm.ledger import Ledger
from staff.review_arm.policy import HELD_PRS, LiveHead, arm_gate
from staff.review_arm.verdicts import count_blocking, extract_outcome

HEAD = "a" * 40
NEW = "b" * 40

HOLDS = """\
# lifted 11-12 07:58 (r2 resolved) Tools 6030 # audit note
Gasification_Model 5873 # audit: may label nonconverged reactor run successful
not-a-number x
Tools_Private 2230 # standing hold
"""
BLOCKED = f"""\
Runner_Dashboard 4332 20126532 seed run-a560f4f95582
Runner_Dashboard 42 {HEAD[:8]} 2026-11-13T09:00 run-x blocking=2
bad line
"""
OVERRIDE = "Repository_Management 2890 0bece07e  # Dieter t1737: override placeholder-job block\n"


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.sqlite")


def test_box_state_import_is_one_time_and_persisted(ledger: Ledger, tmp_path: Path) -> None:
    counts = ledger.import_box_state(holds=HOLDS, blocked=BLOCKED, overrides=OVERRIDE)
    assert counts == {"holds": 2, "blocked": 2, "overrides": 1}
    again = ledger.import_box_state(holds=HOLDS + "Tools 1\n", blocked=BLOCKED, overrides=OVERRIDE)
    assert again == {"skipped": 1}
    reopened = Ledger(tmp_path / "ledger.sqlite")
    assert reopened.holds() == frozenset({("Gasification_Model", 5873), ("Tools_Private", 2230)})


def test_commented_hold_lines_are_not_holds(ledger: Ledger) -> None:
    ledger.import_box_state(holds=HOLDS, blocked="", overrides="")
    assert ("Tools", 6030) not in ledger.holds()


def test_imported_blocked_head_matches_by_prefix_like_the_box(ledger: Ledger) -> None:
    ledger.import_box_state(holds="", blocked=BLOCKED, overrides="")
    assert ledger.is_blocked("Runner_Dashboard", 42, HEAD)
    assert not ledger.is_blocked("Runner_Dashboard", 42, NEW)
    assert ledger.is_blocked("Runner_Dashboard", 4332, "20126532" + "c" * 32)


def test_imported_override_clears_a_blocked_head(ledger: Ledger) -> None:
    sha = "0bece07e" + "d" * 32
    ledger.import_box_state(holds="", blocked=f"Repository_Management 2890 {sha} why\n", overrides=OVERRIDE)
    assert not ledger.is_blocked("Repository_Management", 2890, sha)


def test_blocking_verdict_marks_head_blocked(ledger: Ledger) -> None:
    ledger.mark_blocked("Tools", 7, HEAD, why="run-1 blocking=1")
    assert ledger.is_blocked("Tools", 7, HEAD)
    assert not ledger.is_blocked("Tools", 7, NEW)
    assert ledger.state("Tools", 7, HEAD) == (True, False)


def test_dispatch_rounds_and_pending_runs(ledger: Ledger) -> None:
    ledger.record_dispatch("Tools", 7, HEAD, run_id="run-1", round_no=1, provider="claude")
    ledger.record_dispatch("Tools", 7, NEW, run_id="run-2", round_no=2, provider="codex")
    assert ledger.rounds("Tools", 7) == 2
    assert [d["run_id"] for d in ledger.pending_dispatches()] == ["run-1", "run-2"]
    ledger.mark_handled("run-1", outcome="approve")
    assert [d["run_id"] for d in ledger.pending_dispatches()] == ["run-2"]
    assert ledger.dispatched_at("Tools", 7, NEW)
    assert not ledger.dispatched_at("Tools", 8, NEW)


def test_escalation_is_deduplicated_per_head_and_reason(ledger: Ledger) -> None:
    assert ledger.record_escalation("Tools", 7, HEAD, "round_cap") is True
    assert ledger.record_escalation("Tools", 7, HEAD, "round_cap") is False
    assert ledger.record_escalation("Tools", 7, NEW, "round_cap") is True


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("No [BLOCKING] findings.\n- [NONBLOCKING] nit a.py:3", 0),
        ("- [BLOCKING] SQL injection a.py:9\n- [BLOCKING] b.py:2", 2),
        ("There are no BLOCKING findings.", 0),
        ("**[BLOCKING]** race in x.py:10", 1),
        ("zero [BLOCKING] issues; one [NONBLOCKING]", 0),
        ("", 0),
    ],
)
def test_count_blocking_is_negation_safe(text: str, expected: int) -> None:
    assert count_blocking(text) == expected


def _events(*texts: str) -> list[dict[str, object]]:
    return [{"seq": i, "kind": "result", "text": t} for i, t in enumerate(texts, 1)]


def test_outcome_pending_while_run_active() -> None:
    assert extract_outcome("queued", "", []).kind == "pending"
    assert extract_outcome("running", "", []).kind == "pending"


def test_outcome_from_succeeded_run() -> None:
    events = _events("ok a.py:1\nSTAFF_RESULT: review approve #7")
    out = extract_outcome("succeeded", "review approve #7 @aaaaaaa", events)
    assert (out.kind, out.verdict, out.recovered, out.verdict_head) == ("verdict", "approve", False, "aaaaaaa")
    assert out.text.startswith("ok a.py:1")


def test_outcome_recovered_from_events_after_failed_post_step() -> None:
    text = "Looks right x.py:4\nSTAFF_RESULT: review approve #7 @aaaaaaa"
    out = extract_outcome("failed", "", _events("thinking", text))
    assert (out.kind, out.verdict, out.recovered) == ("verdict", "approve", True)


def test_recovered_approve_with_blocking_findings_becomes_changes() -> None:
    text = "- [BLOCKING] data loss a.py:1\nSTAFF_RESULT: review approve #7"
    out = extract_outcome("failed", "", _events(text))
    assert (out.verdict, out.blocking) == ("changes", 1)


def test_failed_run_without_verdict() -> None:
    assert extract_outcome("failed", "", _events("crashed")).kind == "failed_no_verdict"


def _live(**kw: object) -> LiveHead:
    base: dict[str, object] = {"head_sha": HEAD, "state": "OPEN", "draft": False, "labels": (), "title": "fix"}
    base.update(kw)
    return LiveHead(**base)  # type: ignore[arg-type]


def _gate(live: LiveHead, *, blocked: bool = False, holds: frozenset[tuple[str, int]] = frozenset()) -> str:
    d = arm_gate("Tools", 7, HEAD, live, holds=holds, blocked=blocked)
    return d.reason if d.action != "arm" else "arm"


def test_arm_gate_arms_same_open_unheld_head() -> None:
    assert _gate(_live()) == "arm"


def test_arm_gate_order_matches_armd() -> None:
    assert _gate(_live(labels=("do-not-merge",))) == "hold_label"
    assert _gate(_live(), holds=frozenset({("Tools", 7)})) == "held_pr"
    assert _gate(_live(state="MERGED")) == "not_open"
    assert _gate(_live(draft=True)) == "draft"
    assert _gate(_live(), blocked=True) == "blocked_head"
    assert _gate(_live(head_sha=NEW)) == "head_moved"
    # A hold outranks a moved head (armd checks holds first).
    assert _gate(_live(head_sha=NEW, labels=("hold",))) == "hold_label"


def test_arm_gate_head_moved_requests_requeue() -> None:
    d = arm_gate("Tools", 7, HEAD, _live(head_sha=NEW), holds=frozenset(), blocked=False)
    assert d.action == "requeue"


def test_seeded_holds_include_box_static_holds() -> None:
    for key in [("Tools_Private", 2191), ("Tools_Private", 2195), ("Runner_Dashboard", 4253), ("AffineDrift", 5562)]:
        assert key in HELD_PRS
        assert _gate(_live(), holds=HELD_PRS) == "arm"  # other PRs unaffected


def test_recovery_ignores_prose_that_is_not_a_staff_result_line() -> None:
    out = extract_outcome("failed", "", _events("I will review approve #7 later\nno verdict"))
    assert out.kind == "failed_no_verdict"


def test_attempts_at_head(ledger: Ledger) -> None:
    for run in ("run-1", "run-2", "run-3"):
        ledger.record_dispatch("Tools", 7, HEAD, run_id=run, round_no=1, provider="claude")
    ledger.mark_handled("run-1", outcome="failed_no_verdict")
    ledger.mark_handled("run-2", outcome="approve:arm:exact_head_approval")
    assert ledger.attempts_at("Tools", 7, HEAD) == {"pending": 1, "done": 1, "failed": 1}
    assert ledger.attempts_at("Tools", 7, NEW) == {"pending": 0, "done": 0, "failed": 0}
