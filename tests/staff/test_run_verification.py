"""Post-run verification of staff runs (WP-1.1, #1516).

A run that claims success is checked against GitHub: the PR for its branch must exist
(open or merged) with head CI green. The verdict is recorded next to the run status;
only ``enforce`` mode may change that status, and a GitHub error never does.
"""

from __future__ import annotations

import itertools
import json
import subprocess
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from staff import verification as v
from staff.store import RunRecord, RunStore

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _rec(**overrides: Any) -> RunRecord:
    base: dict[str, Any] = {
        "id": "run_1",
        "role": "night-watch",
        "provider": "fake",
        "model": None,
        "machine": "TestNode",
        "repo": "Runner_Dashboard",
        "target_kind": "issue",
        "target_ref": "42",
        "prompt": "p",
        "status": "succeeded",
        "branch": "staff/night-watch-42",
        "ended_at": _iso(NOW - timedelta(minutes=5)),
    }
    base.update(overrides)
    return RunRecord(**base)


class FakeProbe:
    """A PrProbe returning a fixed answer (or raising) and recording its calls."""

    def __init__(self, pr: v.PullRequest | None = None, error: Exception | None = None) -> None:
        self.pr, self.error, self.calls = pr, error, []

    def find(self, repo: str, branch: str) -> v.PullRequest | None:
        self.calls.append((repo, branch))
        if self.error is not None:
            raise self.error
        return self.pr


# ── the verdict table ────────────────────────────────────────────────────
def _expected(claimed: str, opens_pr: bool, pr_state: str | None, ci: str) -> str:
    if claimed != "succeeded" or not opens_pr:
        return "not_applicable"
    if pr_state is None:
        return "failed"
    return {"green": "verified", "pending": "unverified", "red": "failed"}[ci]


@pytest.mark.parametrize(
    ("claimed", "opens_pr", "pr_state", "ci"),
    list(
        itertools.product(("succeeded", "failed"), (True, False), (None, "open", "merged"), ("green", "pending", "red"))
    ),
)
def test_every_combination_maps_to_its_verdict(claimed: str, opens_pr: bool, pr_state: str | None, ci: str) -> None:
    pr = None if pr_state is None else v.PullRequest(number=7, state=pr_state, ci=ci)

    verdict = v.decide(claimed=claimed, opens_pr=opens_pr, branch="b", pr=pr)

    assert verdict.verification == _expected(claimed, opens_pr, pr_state, ci)
    assert verdict.pr_number == (7 if pr is not None and verdict.verification != "not_applicable" else None)
    assert verdict.detail


def test_a_closed_unmerged_pr_fails() -> None:
    verdict = v.decide(claimed="succeeded", opens_pr=True, branch="b", pr=v.PullRequest(9, "closed", "green"))
    assert (verdict.verification, verdict.pr_number) == ("failed", 9)
    assert "closed" in verdict.detail


def test_no_pr_and_no_branch_explain_themselves() -> None:
    assert "b-1" in v.decide(claimed="succeeded", opens_pr=True, branch="b-1", pr=None).detail
    assert "no branch" in v.decide(claimed="succeeded", opens_pr=True, branch="", pr=None).detail


# ── head CI ──────────────────────────────────────────────────────────────
def _check(status: str = "completed", conclusion: str | None = "success") -> dict[str, Any]:
    return {"status": status, "conclusion": conclusion}


@pytest.mark.parametrize(
    ("runs", "state"),
    [
        ([_check(), _check(conclusion="skipped"), _check(conclusion="neutral")], "green"),
        ([_check(), _check(status="in_progress", conclusion=None)], "pending"),
        ([], "pending"),
        ([_check(), _check(conclusion="failure")], "red"),
        ([_check(status="in_progress", conclusion=None), _check(conclusion="timed_out")], "red"),
        ([_check(conclusion="action_required")], "red"),
        # A cancelled or stale run was superseded; it neither passes nor fails the head.
        ([_check(), _check(conclusion="cancelled")], "green"),
        ([_check(conclusion="cancelled")], "pending"),
    ],
)
def test_ci_state_of_head_check_runs(runs: list[dict[str, Any]], state: str) -> None:
    assert v.ci_state(runs) == state


# ── evaluate: probe errors and the pending limit ─────────────────────────
def test_a_github_error_leaves_the_run_unverified() -> None:
    probe = FakeProbe(error=v.GitHubLookupError("HTTP 502"))

    verdict = v.evaluate(_rec(), opens_pr=True, probe=probe, now=NOW)

    assert verdict.verification == "unverified"
    assert "HTTP 502" in verdict.detail


def test_no_github_call_when_nothing_was_claimed_or_no_pr_is_expected() -> None:
    probe = FakeProbe()
    v.evaluate(_rec(status="failed"), opens_pr=True, probe=probe, now=NOW)
    v.evaluate(_rec(), opens_pr=False, probe=probe, now=NOW)
    v.evaluate(_rec(branch=""), opens_pr=True, probe=probe, now=NOW)
    assert probe.calls == []


def test_ci_still_pending_past_the_limit_fails() -> None:
    old = _rec(ended_at=_iso(NOW - v.PENDING_LIMIT - timedelta(minutes=1)))
    verdict = v.evaluate(old, opens_pr=True, probe=FakeProbe(v.PullRequest(3, "open", "pending")), now=NOW)
    assert (verdict.verification, verdict.pr_number) == ("failed", 3)
    assert "pending" in verdict.detail


def test_a_github_error_past_the_limit_still_never_fails_the_run() -> None:
    old = _rec(ended_at=_iso(NOW - v.PENDING_LIMIT - timedelta(hours=1)))
    verdict = v.evaluate(old, opens_pr=True, probe=FakeProbe(error=v.GitHubLookupError("down")), now=NOW)
    assert verdict.verification == "unverified"


# ── modes ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "mode"), [(None, "report"), ("", "report"), ("enforce", "enforce"), ("OFF", "off"), ("bogus", "report")]
)
def test_verify_mode_defaults_to_report(raw: str | None, mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if raw is None:
        monkeypatch.delenv(v.MODE_ENV, raising=False)
    else:
        monkeypatch.setenv(v.MODE_ENV, raw)
    assert v.verify_mode() == mode


FAILED = v.Verdict("failed", "no PR", None)


@pytest.mark.parametrize(
    ("mode", "verdict", "status_change"),
    [
        ("report", FAILED, False),
        ("enforce", FAILED, True),
        ("enforce", v.Verdict("unverified", "CI pending", 4), False),
        ("enforce", v.Verdict("verified", "green", 4), False),
    ],
)
def test_only_enforce_turns_a_failed_verdict_into_a_failed_run(
    mode: str, verdict: v.Verdict, status_change: bool
) -> None:
    updates = v.updates_for(_rec(), verdict, mode)

    assert updates["verification"] == verdict.verification
    assert updates["verification_detail"] == verdict.detail
    assert updates["pr_number"] == verdict.pr_number
    if status_change:
        assert (updates["status"], updates["failure_class"]) == ("failed", v.UNVERIFIED_OUTPUT)
        assert updates["error"] == verdict.detail
    else:
        assert "status" not in updates and "failure_class" not in updates


def test_enforce_never_touches_a_run_that_did_not_succeed() -> None:
    assert "status" not in v.updates_for(_rec(status="cancelled"), FAILED, "enforce")


# ── recording on the store ───────────────────────────────────────────────
@pytest.fixture
def store(tmp_path: Path) -> Iterator[RunStore]:
    s = RunStore(tmp_path / "runs.sqlite3")
    yield s
    s.close()


def test_verify_and_record_persists_the_verdict_and_an_event(store: RunStore) -> None:
    store.create_run(_rec())
    probe = FakeProbe(v.PullRequest(12, "merged", "green"))

    verdict = v.verify_and_record(store, "run_1", opens_pr=True, probe=probe, mode="report", now=NOW)

    assert verdict is not None and verdict.verification == "verified"
    rec = store.get_run("run_1")
    assert rec is not None
    assert (rec.verification, rec.pr_number, rec.status) == ("verified", 12, "succeeded")
    assert any(e["kind"] == "verify" for e in store.events_after("run_1"))


def test_off_mode_records_nothing_and_calls_nothing(store: RunStore) -> None:
    store.create_run(_rec())
    probe = FakeProbe(v.PullRequest(12, "open", "green"))

    assert v.verify_and_record(store, "run_1", opens_pr=True, probe=probe, mode="off", now=NOW) is None
    assert probe.calls == []
    assert store.get_run("run_1").verification == ""  # type: ignore[union-attr]


def test_verify_and_record_never_raises(store: RunStore) -> None:
    store.create_run(_rec())

    class Exploding:
        def find(self, repo: str, branch: str) -> v.PullRequest | None:
            raise RuntimeError("bug in the probe")

    verdict = v.verify_and_record(store, "run_1", opens_pr=True, probe=Exploding(), mode="enforce", now=NOW)

    assert verdict is not None and verdict.verification == "unverified"
    assert store.get_run("run_1").status == "succeeded"  # type: ignore[union-attr]


def test_new_columns_round_trip(store: RunStore) -> None:
    store.create_run(_rec(verification="failed", verification_detail="x", pr_number=5))
    rec = store.get_run("run_1")
    assert rec is not None and (rec.verification, rec.verification_detail, rec.pr_number) == ("failed", "x", 5)


# ── recheck (reconcile + scheduler) ──────────────────────────────────────
def test_recheck_verifies_recent_unchecked_and_unverified_runs_of_this_node_only(store: RunStore) -> None:
    store.create_run(_rec(id="fresh"))  # finished while nobody checked
    store.create_run(_rec(id="pending", verification="unverified"))
    store.create_run(_rec(id="done", verification="verified"))
    store.create_run(_rec(id="peer", machine="OtherNode"))
    store.create_run(_rec(id="ancient", ended_at=_iso(NOW - v.RECHECK_WINDOW - timedelta(hours=1))))
    store.create_run(_rec(id="active", status="running", ended_at=None))
    probe = FakeProbe(v.PullRequest(8, "open", "green"))

    checked = v.recheck_runs(store, machine="TestNode", opens_pr=lambda role: True, probe=probe, mode="report", now=NOW)

    assert sorted(checked) == ["fresh", "pending"]
    assert store.get_run("pending").verification == "verified"  # type: ignore[union-attr]
    assert store.get_run("ancient").verification == ""  # type: ignore[union-attr]


def test_recheck_is_bounded_per_pass(store: RunStore) -> None:
    for i in range(v.RECHECK_BATCH + 5):
        store.create_run(_rec(id=f"r{i:02d}"))
    checked = v.recheck_runs(
        store, machine="TestNode", opens_pr=lambda role: True, probe=FakeProbe(), mode="report", now=NOW
    )
    assert len(checked) == v.RECHECK_BATCH


def test_recheck_in_off_mode_does_nothing(store: RunStore) -> None:
    store.create_run(_rec())
    assert v.recheck_runs(store, machine="TestNode", opens_pr=lambda r: True, probe=FakeProbe(), mode="off") == []


# ── the gh CLI probe ─────────────────────────────────────────────────────
def _gh(responses: dict[str, Any]) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        path = argv[2]
        for key, body in responses.items():
            if key in path:
                if isinstance(body, int):
                    return subprocess.CompletedProcess(argv, body, "", "HTTP 502")
                return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
        raise AssertionError(f"unexpected gh call {argv}")

    return run


def test_gh_probe_finds_the_newest_pr_for_the_branch_and_its_head_ci() -> None:
    calls: list[list[str]] = []
    pulls = [{"number": 21, "state": "closed", "merged_at": "2026-09-25T10:00:00Z", "head": {"sha": "abc"}}]
    checks = {"check_runs": [_check(), _check(conclusion="failure")]}
    runner = _gh({"/pulls?": pulls, "/commits/abc/check-runs": checks})

    def recording(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return runner(argv, **kw)

    pr = v.GhCliPrProbe(org="D-sorganization", run=recording).find("Runner_Dashboard", "staff/x y")

    assert pr == v.PullRequest(21, "merged", "red")
    assert "/repos/D-sorganization/Runner_Dashboard/pulls?head=D-sorganization:staff%2Fx%20y&state=all" in calls[0][2]
    assert len(calls) == 2  # one scoped lookup plus the head's check runs; never a sweep


def test_gh_probe_returns_none_without_a_pr() -> None:
    assert v.GhCliPrProbe(org="o", run=_gh({"/pulls?": []})).find("r", "b") is None


@pytest.mark.parametrize("failure", ["exit", "bad-json", "missing-gh"])
def test_gh_probe_failures_raise_github_lookup_error(failure: str) -> None:
    def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        if failure == "missing-gh":
            raise FileNotFoundError("gh")
        if failure == "exit":
            return subprocess.CompletedProcess(argv, 1, "", "HTTP 502: Bad Gateway")
        return subprocess.CompletedProcess(argv, 0, "not json", "")

    with pytest.raises(v.GitHubLookupError):
        v.GhCliPrProbe(org="o", run=run).find("r", "b")


# ── who triggers a recheck ───────────────────────────────────────────────
def test_the_scheduler_rechecks_at_most_once_per_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    from staff import scheduler as sched_mod  # noqa: PLC0415

    calls: list[str] = []
    monkeypatch.setattr(v, "recheck_runs", lambda store, **kw: calls.append(kw["machine"]) or [])
    runner = SimpleNamespace(store=object(), machine="TestNode", opens_pr=lambda role: True)
    scheduler = sched_mod.StaffScheduler(runner, holds=None, budget=None)  # type: ignore[arg-type]

    scheduler.recheck_verification()
    scheduler.recheck_verification()

    assert calls == ["TestNode"]


def test_startup_reconcile_rechecks_runs_that_finished_unchecked(
    monkeypatch: pytest.MonkeyPatch, store: RunStore
) -> None:
    from staff import reconcile  # noqa: PLC0415

    calls: list[str] = []
    monkeypatch.setattr(v, "recheck_runs", lambda s, **kw: calls.append(kw["machine"]) or [])
    runner = SimpleNamespace(store=store, machine="TestNode", opens_pr=lambda role: True)

    reconcile.reconcile_orphaned_runs(runner, event_store=object())  # type: ignore[arg-type]

    assert calls == ["TestNode"]
