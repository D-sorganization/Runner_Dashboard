"""When STAFF_RESULT is missing, a pushed branch or open PR still succeeds (#2773)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.execution_result import classify_execution_result
from staff.store import RunRecord
from staff.verification import PullRequest


class _Probe:
    def __init__(self, pr: PullRequest | None = None, ahead: int = 0) -> None:
        self.pr = pr
        self.ahead = ahead

    def find(self, repo: str, branch: str) -> PullRequest | None:
        return self.pr

    def remote_ahead_by(self, repo: str, branch: str, base: str = "main") -> int:
        return self.ahead


@pytest.mark.unit
def test_classify_succeeds_without_staff_result_when_pr_exists(tmp_path: Path) -> None:
    transcript = tmp_path / "t.log"
    transcript.write_text("agent finished push\n", encoding="utf-8")
    rec = RunRecord(
        id="run-pr",
        role="night-watch",
        provider="codex",
        model="m",
        machine="n",
        repo="UpstreamDrift",
        target_kind="issue",
        target_ref="42",
        prompt="go",
        branch="staff/night-watch-42",
    )
    probe = _Probe(pr=PullRequest(number=2770, state="open", ci="pending"), ahead=0)
    status, failure_class, retryable, remediation, error = classify_execution_result(
        cancelled=False,
        rc=0,
        result_line="",
        provider="codex",
        transcript_path=transcript,
        run=rec,
        opens_pr=True,
        probe=probe,
    )
    assert status == "succeeded"
    assert failure_class == ""
    assert not retryable
    assert remediation == ""
    assert error == ""


@pytest.mark.unit
def test_classify_ignores_phantom_nudge_staff_result_when_pr_exists(tmp_path: Path) -> None:
    transcript = tmp_path / "t.log"
    transcript.write_text("nudge path\n", encoding="utf-8")
    rec = RunRecord(
        id="run-phantom",
        role="night-watch",
        provider="codex",
        model="m",
        machine="n",
        repo="UpstreamDrift",
        target_kind="issue",
        target_ref="99",
        prompt="go",
        branch="staff/night-watch-99",
    )
    probe = _Probe(pr=PullRequest(number=99, state="open", ci="green"), ahead=0)
    status, failure_class, _, _, _ = classify_execution_result(
        cancelled=False,
        rc=0,
        result_line="STAFF_RESULT: No repository task was specified.",
        provider="codex",
        transcript_path=transcript,
        run=rec,
        opens_pr=True,
        probe=probe,
    )
    assert status == "succeeded"
    assert failure_class == ""


@pytest.mark.unit
def test_classify_stays_no_result_without_deliverable(tmp_path: Path) -> None:
    transcript = tmp_path / "t.log"
    transcript.write_text("done\n", encoding="utf-8")
    rec = RunRecord(
        id="run-empty",
        role="night-watch",
        provider="codex",
        model="m",
        machine="n",
        repo="UpstreamDrift",
        target_kind="issue",
        target_ref="1",
        prompt="go",
        branch="staff/night-watch-empty",
    )
    probe = _Probe(pr=None, ahead=0)
    status, failure_class, _, _, _ = classify_execution_result(
        cancelled=False,
        rc=0,
        result_line="",
        provider="codex",
        transcript_path=transcript,
        run=rec,
        opens_pr=True,
        probe=probe,
    )
    assert status == "failed"
    assert failure_class == "no_result"
