"""CI grace window, skip results and recheck horizon for post-run verification.

Regression: run-7994f9e1b527 pushed and opened a PR at 14:21:28Z, exited at 14:22:15 and was
verified at 14:22:16 while CI was (transiently) red, so it was failed for good although the PR merged
green. A legitimate "no work requested" skip was likewise scored failed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from staff import verification as v
from staff.run_outcome import classify_outcome
from staff.store import RunRecord, RunStore

NOW = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _rec(**overrides: Any) -> RunRecord:
    base: dict[str, Any] = {
        "id": "run-a",
        "role": "pr-remediator",
        "provider": "fake",
        "model": None,
        "machine": "TestNode",
        "repo": "UpstreamDrift",
        "target_kind": "pr",
        "target_ref": "13652",
        "prompt": "p",
        "status": "succeeded",
        "branch": "staff/pr-remediator-13652",
        "ended_at": _iso(NOW - timedelta(minutes=1)),
    }
    base.update(overrides)
    return RunRecord(**base)


class Probe:
    def __init__(self, pr: v.PullRequest | None, ahead: int = 1) -> None:
        self.pr, self.ahead, self.calls = pr, ahead, 0

    def find(self, repo: str, branch: str) -> v.PullRequest | None:
        self.calls += 1
        return self.pr

    def remote_ahead_by(self, repo: str, branch: str, base: str = "main") -> int:
        return self.ahead


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RunStore]:
    s = RunStore(tmp_path / "runs.sqlite3")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _default_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(v.CI_GRACE_ENV, raising=False)


def _red(state: str = "open") -> v.PullRequest:
    return v.PullRequest(13652, state, "red")


# -- CI race -----------------------------------------------------------------
def test_red_ci_one_minute_after_run_ended_is_unverified() -> None:
    verdict = v.evaluate(_rec(), opens_pr=True, probe=Probe(_red()), now=NOW)
    assert verdict.verification == "unverified"
    assert verdict.pr_number == 13652


def test_red_ci_after_grace_is_failed_with_ci_remediation() -> None:
    rec = _rec(ended_at=_iso(NOW - timedelta(minutes=45)))
    verdict = v.evaluate(rec, opens_pr=True, probe=Probe(_red()), now=NOW)
    assert verdict.verification == "failed"
    assert verdict.ci_red
    updates = v.deliverable_failure_updates(rec, verdict, opens_pr=True)
    assert "no commits or open PR" not in updates["remediation"]
    assert "CI" in updates["remediation"]


def test_grace_seconds_env_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(v.CI_GRACE_ENV, "30")
    assert v.evaluate(_rec(), opens_pr=True, probe=Probe(_red()), now=NOW).verification == "failed"
    monkeypatch.setenv(v.CI_GRACE_ENV, "garbage")
    assert v.ci_grace() == timedelta(seconds=v.DEFAULT_CI_GRACE_SECONDS)


def test_deliverable_failure_updates_is_empty_for_pending_ci() -> None:
    verdict = v.evaluate(_rec(), opens_pr=True, probe=Probe(_red()), now=NOW)
    assert v.deliverable_failure_updates(_rec(), verdict, opens_pr=True) == {}


def test_recheck_flips_ci_failed_run_to_verified_and_restores_status(store: RunStore) -> None:
    rec = _rec(
        status="failed",
        failure_class=v.UNVERIFIED_OUTPUT,
        error="PR #13652 head CI is failing",
        verification="failed",
        verification_detail="PR #13652 head CI is failing",
        pr_number=13652,
        ended_at=_iso(NOW - timedelta(hours=2)),
    )
    store.create_run(rec)
    probe = Probe(v.PullRequest(13652, "merged", "green"))

    checked = v.recheck_runs(store, machine="TestNode", opens_pr=lambda role: True, probe=probe, mode="report", now=NOW)

    assert checked == ["run-a"]
    got = store.get_run("run-a")
    assert got is not None
    assert (got.verification, got.status, got.failure_class, got.error) == ("verified", "succeeded", "", "")


def test_recheck_keeps_unrelated_failed_runs_out(store: RunStore) -> None:
    store.create_run(_rec(id="crash", status="failed", failure_class="timeout", verification="not_applicable"))
    store.create_run(
        _rec(id="noPR", status="failed", verification="failed", verification_detail="no pull request found")
    )
    probe = Probe(None)
    assert v.recheck_runs(store, machine="TestNode", opens_pr=lambda r: True, probe=probe, mode="report", now=NOW) == []
    assert probe.calls == 0


# -- skip / no-op ------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "STAFF_RESULT: No work was requested",
        "STAFF_RESULT: skipped: PR already merged",
        "STAFF_RESULT: Nothing to do",
        "noise\nSTAFF_RESULT: STAFF_SKIP no actionable PR",
    ],
)
def test_skip_markers_match(text: str) -> None:
    assert v.is_skip_result(text)


@pytest.mark.parametrize(
    "text",
    ["", "no marker here", "STAFF_RESULT: fixed 3 failing tests", "STAFF_RESULT: pushed fix, skipped flaky test"],
)
def test_non_skip_results_do_not_match(text: str) -> None:
    assert not v.is_skip_result(text)


def test_skip_with_nothing_pushed_is_not_applicable_and_status_stays_succeeded(store: RunStore) -> None:
    store.create_run(_rec())
    store.append_event("run-a", "text", "STAFF_RESULT: No work was requested")
    verdict = v.verify_and_record(store, "run-a", opens_pr=True, probe=Probe(None, ahead=0), mode="enforce", now=NOW)
    assert verdict is not None
    assert verdict.verification == "not_applicable"
    rec = store.get_run("run-a")
    assert rec is not None
    assert rec.status == "succeeded"
    assert v.deliverable_failure_updates(rec, verdict, opens_pr=True) == {}
    assert classify_outcome(rec.status, rec.verification, rec.failure_class) == "no_op"


def test_no_result_line_and_nothing_pushed_stays_no_change() -> None:
    verdict = v.evaluate(_rec(), opens_pr=True, probe=Probe(None, ahead=0), now=NOW)
    assert verdict.verification == "no_change"


def test_skip_text_does_not_hide_pushed_commits_without_pr() -> None:
    verdict = v.evaluate(
        _rec(), opens_pr=True, probe=Probe(None, ahead=2), now=NOW, result_text="STAFF_RESULT: skipped"
    )
    assert verdict.verification == "failed"


# -- shadow-routing classifier ----------------------------------------------
def test_classifier_keeps_pending_ci_and_skips_neutral() -> None:
    assert classify_outcome("succeeded", "unverified", "") == "no_op"
    assert classify_outcome("succeeded", "not_applicable", "") == "no_op"
    assert classify_outcome("succeeded", "verified", "") == "success"
    assert classify_outcome("failed", "failed", v.UNVERIFIED_OUTPUT) == "quality_fail"


# -- stale rows --------------------------------------------------------------
def test_stale_unverified_rows_beyond_horizon_are_left_alone(store: RunStore) -> None:
    old = _iso(NOW - v.RECHECK_WINDOW - timedelta(hours=1))
    for i in range(30):
        store.create_run(_rec(id=f"old{i}", verification="unverified", ended_at=old))
    probe = Probe(v.PullRequest(1, "open", "pending"))

    checked = v.recheck_runs(store, machine="TestNode", opens_pr=lambda r: True, probe=probe, mode="enforce", now=NOW)

    assert checked == []
    assert probe.calls == 0
    assert all(store.get_run(f"old{i}").status == "succeeded" for i in range(30))  # type: ignore[union-attr]
