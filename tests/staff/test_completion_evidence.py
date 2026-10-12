"""Run completion is derived from typed evidence, never from summary prose (#1808)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from staff import verification as v
from staff.completion import ARTIFACT_KINDS, work_item_state_for_success
from staff.run_link import handle_run_status_change
from staff.store import RunRecord, RunStore, reset_store
from staff.work_items import (
    VALID_TRANSITIONS,
    WORK_ITEM_STATES,
    WorkItemStore,
    get_work_item_store,
    reset_work_item_store,
)


@pytest.mark.parametrize(
    ("artifact", "state"),
    [
        ("pr_open", "waiting_on_ci"),
        ("pr_merged", "done"),
        ("deployed", "done"),
        ("", "needs_evidence"),
        ("garbage", "needs_evidence"),
    ],
)
def test_state_mapping(artifact: str, state: str) -> None:
    assert work_item_state_for_success(artifact) == state


def test_artifact_kinds_are_closed() -> None:
    assert set(ARTIFACT_KINDS) == {"pr_open", "pr_merged", "deployed"}


def test_needs_evidence_is_a_non_terminal_state() -> None:
    assert "needs_evidence" in WORK_ITEM_STATES
    assert "needs_evidence" in VALID_TRANSITIONS["in_progress"]
    assert VALID_TRANSITIONS["needs_evidence"]  # can leave the state


# ── verification records the typed artifact ──────────────────────────────
@pytest.mark.parametrize(
    ("state", "ci", "artifact"),
    [("open", "green", "pr_open"), ("open", "pending", "pr_open"), ("merged", "green", "pr_merged")],
)
def test_decide_records_artifact(state: str, ci: str, artifact: str) -> None:
    pr = v.PullRequest(7, state, ci)
    verdict = v.decide(claimed="succeeded", opens_pr=True, branch="b", pr=pr)
    assert verdict.artifact == artifact


@pytest.mark.parametrize("pr", [None, v.PullRequest(7, "closed", "green")])
def test_decide_no_artifact_without_live_pr(pr: v.PullRequest | None) -> None:
    assert v.decide(claimed="succeeded", opens_pr=True, branch="b", pr=pr).artifact == ""


def test_updates_for_writes_artifact() -> None:
    rec = RunRecord("r", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status="succeeded")
    assert v.updates_for(rec, v.Verdict("verified", "ok", 3, "pr_merged"), "report")["artifact"] == "pr_merged"


# ── run-link lifecycle ───────────────────────────────────────────────────
@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[WorkItemStore]:
    db = tmp_path / "runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    reset_store()
    reset_work_item_store()
    yield get_work_item_store(db)
    reset_work_item_store()
    reset_store()


def _final_state(store: WorkItemStore, artifact: str, summary: str) -> str:
    wi = store.create_work_item(title="t", requested_by="op")
    run = RunRecord(
        "run-e", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", work_item_id=wi.id, artifact=artifact
    )
    handle_run_status_change(run, "running")
    handle_run_status_change(run, "succeeded", summary=summary)
    got = store.get_work_item(wi.id)
    assert got is not None
    return got.state


@pytest.mark.parametrize(
    ("artifact", "state"),
    [("pr_open", "waiting_on_ci"), ("pr_merged", "done"), ("deployed", "done"), ("", "needs_evidence")],
)
def test_succeeded_run_state_follows_artifact(store: WorkItemStore, artifact: str, state: str) -> None:
    assert _final_state(store, artifact, "Resolved issue #42") == state


def test_summary_prose_cannot_change_state(store: WorkItemStore) -> None:
    for prose in ("Opened PR #123", "see https://github.com/o/r/pull/9", "all good"):
        assert _final_state(store, "", prose) == "needs_evidence"
        assert _final_state(store, "pr_merged", prose) == "done"


def test_run_store_roundtrips_artifact(tmp_path: Path) -> None:
    s = RunStore(tmp_path / "r.sqlite3")
    try:
        assert "artifact" in s.columns()
        s.create_run(RunRecord("r1", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p"))
        s.update_run("r1", artifact="pr_open")
        got = s.get_run("r1")
        assert got is not None and got.artifact == "pr_open"
    finally:
        s.close()
