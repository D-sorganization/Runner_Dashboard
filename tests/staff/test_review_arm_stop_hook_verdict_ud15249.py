"""Refs D-sorganization/UpstreamDrift#15249.

A code-reviewer run may print STAFF_RESULT and then end ``failed`` because an unrelated
Stop-hook ruff failure ran against untouched main debt. The hub must still bind the
verdict to the PR head and relay findings (review-arm ingest + Exact-head relay).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from staff.review import maybe_finalize_code_reviewer_run
from staff.review_arm.ledger import Ledger
from staff.review_arm.policy import LiveHead
from staff.review_arm.processor import VerdictProcessor
from staff.review_arm.verdicts import RunOutcome, extract_outcome

HEAD = "d" * 40
REPO = "Runner_Dashboard"
_PR = 42
STOP_HOOK_TAIL = "Stop hook failed: exit 2\ntrusted_agent_hook.py: ruff failed — backend/legacy_debt.py: E501"


def _staff_result(verdict: str = "approve") -> str:
    return f"STAFF_RESULT: review {verdict} #{_PR} @{HEAD[:7]}"


def _review_body() -> str:
    return f"Reviewed foo.py:1; no [BLOCKING] findings on the PR diff.\n{_staff_result()}\n{STOP_HOOK_TAIL}"


@dataclass
class FakePort:
    outcomes: dict[str, RunOutcome] = field(default_factory=dict)
    live_head: LiveHead = field(default_factory=lambda: LiveHead(HEAD, "OPEN", False))
    armed: list[tuple[str, int, str]] = field(default_factory=list)
    posted: list[tuple[str, int, str, str]] = field(default_factory=list)

    def run_outcome(self, run_id: str) -> RunOutcome:
        return self.outcomes[run_id]

    def live(self, repo: str, pr: int) -> LiveHead:
        return self.live_head

    def arm(self, repo: str, pr: int, head: str, *, holds: frozenset[tuple[str, int]]) -> Any:
        self.armed.append((repo, pr, head))
        from staff.review_arm.armer import ArmOutcome

        return ArmOutcome(True, "armed")

    def post_findings(self, repo: str, pr: int, head: str, run_id: str, text: str) -> None:
        self.posted.append((repo, pr, head, run_id))

    def escalate(self, repo: str, pr: int, head: str, reason: str, detail: str) -> None:
        pass


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.sqlite")


def test_extract_outcome_recovers_verdict_after_stop_hook_failure() -> None:
    """Post-run failure with STAFF_RESULT in the last result event still yields a verdict."""
    events = [
        {"kind": "assistant", "text": "Reading diff…"},
        {"kind": "result", "text": _review_body()},
        {"kind": "error", "text": STOP_HOOK_TAIL},
    ]
    out = extract_outcome("failed", "", events)
    assert out.kind == "verdict"
    assert out.verdict == "approve"
    assert out.recovered is True
    assert out.verdict_head == HEAD[:7].lower()


def test_processor_ingests_and_relays_after_stop_hook_failed_run(ledger: Ledger) -> None:
    """Review-arm must record the verdict and relay when the staff run failed after STAFF_RESULT."""
    ledger.record_dispatch(REPO, _PR, HEAD, run_id="run-stop-hook", round_no=1, provider="claude")
    events = [{"kind": "result", "text": _review_body()}]
    out = extract_outcome("failed", "", events)
    port = FakePort(outcomes={"run-stop-hook": out})
    proc = VerdictProcessor(ledger, port, dry_run=False)
    dispatch = proc.pending()[0]
    result = proc.process(dispatch)
    assert result is not None
    assert result.verdict == "approve"
    assert result.decision.action == "arm"
    assert port.posted == [(REPO, _PR, HEAD, "run-stop-hook")]
    assert ledger.pending_dispatches() == []


class _Store:
    def __init__(self, *, status: str, outcome: str, events: list[dict[str, Any]]) -> None:
        self._status = status
        self._outcome = outcome
        self._events = events

    def get_run(self, run_id: str) -> SimpleNamespace | None:
        return SimpleNamespace(status=self._status, outcome=self._outcome)

    def events_after(self, run_id: str, after: int, limit: int) -> list[dict[str, Any]]:
        return [e for e in self._events if int(e.get("seq", 0)) > after][:limit]


def test_hub_port_run_outcome_recovers_from_failed_run(ledger: Ledger) -> None:
    from staff.review_arm.hub_port import RDHubPort
    from staff.review_arm.settings import Settings

    events = [{"seq": 1, "kind": "result", "text": _review_body()}]
    store = _Store(status="failed", outcome="", events=events)
    import asyncio

    settings = Settings(enabled=True, ledger_path=ledger.path)
    loop = asyncio.new_event_loop()
    try:
        hub = RDHubPort(settings=settings, ledger=ledger, loop=loop, store=store, notify=lambda *a: None)
        out = hub.run_outcome("run-1")
    finally:
        loop.close()
    assert out.kind == "verdict"
    assert out.verdict == "approve"


def test_maybe_finalize_posts_verdict_when_rc_nonzero_but_staff_result_present() -> None:
    """Stop hook may force a non-zero exit even after STAFF_RESULT; publication must still run."""
    events: list[Any] = []

    class Store:
        def append_event(self, _run: str, _kind: str, _text: str) -> None:
            events.append((_kind, _text))

    store = Store()
    with (
        patch("staff.independent_exact_head_verdict.fetch_pull_head_sha", return_value=HEAD),
        patch("staff.staff_github_pr_review.ensure_runner_dashboard_formal_approve") as approve,
    ):
        status, failure_class, *_rest = maybe_finalize_code_reviewer_run(
            role="code-reviewer",
            rc=2,
            store=store,
            run_id="run-x",
            status="failed",
            failure_class="environment_error",
            retryable=False,
            error=STOP_HOOK_TAIL,
            remediation="fix hook",
            result_line=_staff_result(),
            same_provider=False,
            repo=REPO,
            reviewer_model="gpt-test",
            expected_pr_number=_PR,
        )
    approve.assert_called_once()
    assert status == "failed"
    assert failure_class == "environment_error"


def test_maybe_finalize_posts_independent_pass_when_failed_after_staff_result() -> None:
    result = f"STAFF_RESULT: independent_review PASS #{_PR} @{HEAD}"

    class Store:
        def append_event(self, *_a: Any) -> None:
            pass

    with (
        patch("staff.independent_exact_head_verdict.fetch_pull_head_sha", return_value=HEAD),
        patch("staff.independent_exact_head_verdict.publish_independent_exact_head_verdict") as publish,
    ):
        status, failure_class, *_ = maybe_finalize_code_reviewer_run(
            role="code-reviewer",
            rc=0,
            store=Store(),
            run_id="run-ind",
            status="failed",
            failure_class="environment_error",
            retryable=False,
            error=STOP_HOOK_TAIL,
            remediation="",
            result_line=result,
            same_provider=False,
            repo=REPO,
            reviewer_model="gpt-test",
            expected_pr_number=_PR,
        )
    publish.assert_called_once()
    assert publish.call_args.kwargs["head_sha"] == HEAD
    assert status == "failed"
    assert failure_class == "environment_error"


# --- Negative cases (RD #4644): only a STAFF_RESULT contract line may survive a failed run.

ORDINARY_FAILURE = "API Error: 529 overloaded; reviewer aborted before completing the review"


@pytest.mark.parametrize(
    "events",
    [
        # Crashed mid-review: partial prose, no contract line, non-hook error.
        [
            {"kind": "assistant", "text": "Reading diff… foo.py looks fine so far, leaning approve."},
            {"kind": "error", "text": ORDINARY_FAILURE},
        ],
        # Verdict-looking prose without the STAFF_RESULT: prefix is not a contract line.
        [{"kind": "result", "text": f"review approve #{_PR} @{HEAD[:7]}\nLGTM"}],
        # No events at all (killed before any output).
        [],
    ],
)
def test_extract_outcome_ordinary_failure_yields_no_verdict(events: list[dict[str, Any]]) -> None:
    out = extract_outcome("failed", "", events)
    assert out.kind == "failed_no_verdict"
    assert out.verdict == ""
    assert out.recovered is False


@pytest.mark.parametrize("status", ["cancelled", "timed_out", "interrupted"])
def test_extract_outcome_partial_non_failed_status_never_recovers(status: str) -> None:
    """Event-text recovery is reserved for ``failed``; other terminal states need the outcome field."""
    out = extract_outcome(status, "", [{"kind": "result", "text": _review_body()}])
    assert out.kind == "no_verdict"
    assert out.verdict == ""


@pytest.mark.parametrize(
    "out",
    [
        extract_outcome("failed", "", [{"kind": "error", "text": ORDINARY_FAILURE}]),
        extract_outcome("cancelled", "", [{"kind": "result", "text": _review_body()}]),
    ],
    ids=["failed_no_staff_result", "cancelled_partial"],
)
def test_processor_ordinary_failed_or_partial_run_cannot_arm(ledger: Ledger, out: RunOutcome) -> None:
    ledger.record_dispatch(REPO, _PR, HEAD, run_id="run-ordinary", round_no=1, provider="claude")
    port = FakePort(outcomes={"run-ordinary": out})
    proc = VerdictProcessor(ledger, port, dry_run=False)
    result = proc.process(proc.pending()[0])
    assert result is not None
    assert result.decision.action == "requeue"
    assert result.verdict == ""
    assert port.armed == []
    assert port.posted == []
    assert ledger.pending_dispatches() == []


def test_maybe_finalize_ordinary_failure_without_staff_result_publishes_nothing() -> None:
    class Store:
        def append_event(self, *_a: Any) -> None:
            raise AssertionError("no verdict event may be recorded without STAFF_RESULT")

    with (
        patch("staff.independent_exact_head_verdict.fetch_pull_head_sha", return_value=HEAD) as head,
        patch("staff.staff_github_pr_review.ensure_runner_dashboard_formal_approve") as approve,
        patch("staff.independent_exact_head_verdict.publish_independent_exact_head_verdict") as publish,
    ):
        status, failure_class, *_ = maybe_finalize_code_reviewer_run(
            role="code-reviewer",
            rc=1,
            store=Store(),
            run_id="run-ordinary",
            status="failed",
            failure_class="provider_error",
            retryable=True,
            error=ORDINARY_FAILURE,
            remediation="retry",
            result_line="",
            same_provider=False,
            repo=REPO,
            reviewer_model="gpt-test",
            expected_pr_number=_PR,
        )
    approve.assert_not_called()
    publish.assert_not_called()
    head.assert_not_called()
    assert status == "failed"
    assert failure_class == "provider_error"
