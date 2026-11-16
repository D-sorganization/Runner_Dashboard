"""#4249 slice: review dedupe and stale verdict rejection at exact PR head SHA."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from coordination.merge_shepherd_exact_head import collect_review_passes_from_store, pass_at_head, shepherd_arm_eligibility
from staff.review import REVIEWER_ROLE, _already_reviewed, auto_review_if_eligible
from staff.store import RunRecord, RunStore

pytestmark = pytest.mark.unit

_REPO = "Runner_Dashboard"
_PR = 42
_HEAD = "a" * 40
_NEW_HEAD = "b" * 40
_ENDED = "2026-11-16T12:00:00+00:00"
_NOW = datetime(2026, 11, 16, 13, tzinfo=UTC)


def _review_run(
    run_id: str,
    *,
    head_sha: str,
    status: str = "succeeded",
    outcome: str | None = None,
) -> RunRecord:
    line = outcome or f"STAFF_RESULT: review approve #{_PR} @{head_sha}"
    return RunRecord(
        id=run_id,
        role=REVIEWER_ROLE,
        provider="codex",
        model="gpt-test",
        machine="hub",
        repo=_REPO,
        target_kind="pr",
        target_ref=f"PR #{_PR}",
        prompt="review",
        status=status,
        outcome=line if status == "succeeded" else "",
        last_line=line,
        ended_at=_ENDED,
        pr_number=_PR,
    )


class _HeadProbe:
    def __init__(self, head_sha: str) -> None:
        self.head_sha = head_sha
        self.calls: list[tuple[str, int]] = []

    def get_commit_messages(self, repo: str, pr_number: int) -> list[str]:
        return []

    def get_pull_head_sha(self, repo: str, pr_number: int) -> str:
        self.calls.append((repo, pr_number))
        return self.head_sha


@pytest.fixture
def store(tmp_path: Any) -> RunStore:
    return RunStore(tmp_path / "runs.sqlite3")


def test_already_reviewed_ignores_succeeded_verdict_at_an_old_head(store: RunStore) -> None:
    store.create_run(_review_run("old-review", head_sha=_HEAD))
    assert not _already_reviewed(store, _REPO, _PR, head_sha=_NEW_HEAD)


def test_already_reviewed_counts_succeeded_verdict_at_the_current_head(store: RunStore) -> None:
    store.create_run(_review_run("current-review", head_sha=_HEAD))
    assert _already_reviewed(store, _REPO, _PR, head_sha=_HEAD)


def test_already_reviewed_still_blocks_while_a_review_is_in_flight(store: RunStore) -> None:
    store.create_run(_review_run("in-flight", head_sha=_HEAD, status="running", outcome=None))
    assert _already_reviewed(store, _REPO, _PR, head_sha=_NEW_HEAD)


def test_collect_review_passes_ignores_approve_at_a_stale_head() -> None:
    store = SimpleNamespace(
        list_runs=lambda limit=500: [
            SimpleNamespace(
                repo=_REPO,
                role=REVIEWER_ROLE,
                status="succeeded",
                outcome=f"STAFF_RESULT: review approve #{_PR} @{_HEAD}",
                last_line="",
                ended_at=_ENDED,
                created_at=_ENDED,
                model="gpt-test",
            )
        ]
    )
    passes = collect_review_passes_from_store(store, _REPO)
    assert len(passes) == 1
    ok, detail = pass_at_head(passes, pr=_PR, head_sha=_NEW_HEAD, now=_NOW)
    assert not ok and detail == "missing_pass_at_head"


def test_auto_review_queues_after_push_when_only_stale_verdict_exists(
    store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STAFF_AUTO_REVIEW", "1")
    store.create_run(_review_run("old-review", head_sha=_HEAD))
    author = RunRecord(
        id="author-run",
        role="pragmatic-programmer",
        provider="codex",
        model=None,
        machine="TestNode",
        repo=_REPO,
        target_kind="pr",
        target_ref="42",
        prompt="work",
        pr_number=_PR,
    )
    store.create_run(author)

    submitted: list[Any] = []
    runner = MagicMock()
    runner.roles.return_value = {"code-reviewer": SimpleNamespace(providers=("gemini",))}
    runner.store = store

    def _submit(req: Any) -> RunRecord:
        submitted.append(req)
        model = req.model if isinstance(req.model, (str, type(None))) else None
        rec = RunRecord(
            id="queued-review",
            role=req.role,
            provider=req.provider,
            model=model,
            machine="TestNode",
            repo=_REPO,
            target_kind="pr",
            target_ref=f"PR #{_PR}",
            prompt=req.prompt,
            status="queued",
            pr_number=_PR,
        )
        store.create_run(rec)
        return rec

    runner.submit.side_effect = _submit
    probe = _HeadProbe(_NEW_HEAD)

    queued = auto_review_if_eligible(
        author,
        SimpleNamespace(verification="verified", pr_number=_PR),
        store=store,
        runner=runner,
        gh_probe=probe,
    )
    assert queued is True
    assert len(submitted) == 1
    assert probe.calls == [(_REPO, _PR)]


def test_shepherd_arm_rejects_stale_store_pass_at_current_head(store: RunStore) -> None:
    store.create_run(_review_run("old-review", head_sha=_HEAD))
    kind, detail = shepherd_arm_eligibility(
        review_decision="APPROVED",
        file_paths=["backend/x.py"],
        additions=10,
        deletions=2,
        line_cap=300,
        labels=(),
        checks_state="SUCCESS",
        files_complete=True,
        pr_number=_PR,
        head_sha=_NEW_HEAD,
        review_passes=collect_review_passes_from_store(store, _REPO),
        now=_NOW,
    )
    assert kind == "no_approval"
    assert detail == "missing_pass_at_head"