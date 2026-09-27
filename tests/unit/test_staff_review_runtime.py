"""Runtime wiring of the code-reviewer (#1579, follow-up to WP-1.3 #1518).

The #1518 unit tests mocked the store and the dispatch path, so five runtime defects
passed them. These tests use a real ``RunStore`` and the real selection inputs.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from staff import review
from staff.plan import RunRequest
from staff.review import (
    SAME_PROVIDER_TAG,
    auto_review_if_eligible,
    detect_author_provider,
    is_same_provider_review,
    parse_outcome,
    prepare_review_params,
)
from staff.store import RunRecord, RunStore


def _run(run_id: str, **kwargs: Any) -> RunRecord:
    defaults: dict[str, Any] = {
        "id": run_id,
        "role": "pragmatic-programmer",
        "provider": "codex",
        "model": None,
        "machine": "TestNode",
        "repo": "Runner_Dashboard",
        "target_kind": "prompt",
        "target_ref": "",
        "prompt": "work",
    }
    defaults.update(kwargs)
    return RunRecord(**defaults)


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "runs.sqlite3")


def _add(store: RunStore, rec: RunRecord, **updates: Any) -> RunRecord:
    store.create_run(rec)
    if updates:
        store.update_run(rec.id, **updates)
    return rec


class _Trailers:
    """Commit-trailer probe stub: the PR's commit messages."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.calls: list[tuple[str, int]] = []

    def get_commit_messages(self, repo: str, pr_number: int) -> list[str]:
        self.calls.append((repo, pr_number))
        return self.messages


def _roster(*providers: str) -> dict[str, Any]:
    return {"code-reviewer": SimpleNamespace(providers=tuple(providers))}


# ── defect 1: the author lookup works against a real RunStore ─────────────────
@pytest.mark.unit
def test_detect_author_provider_reads_a_real_run_store(store: RunStore) -> None:
    _add(store, _run("run-author", provider="codex"), pr_number=42)
    # Same PR number in another repo must not be taken as the author.
    _add(store, _run("run-other-repo", provider="gemini", repo="Tools"), pr_number=42)

    assert detect_author_provider("Runner_Dashboard", 42, store=store) == "codex"


@pytest.mark.unit
def test_detect_author_provider_matches_on_branch_and_skips_reviewer_runs(store: RunStore) -> None:
    _add(store, _run("run-review", role="code-reviewer", provider="claude"), pr_number=7)
    _add(store, _run("run-author", provider="gemini", branch="staff/fix-7"))

    assert detect_author_provider("Runner_Dashboard", 7, branch="staff/fix-7", store=store) == "gemini"


# ── defect 2: the role's providers and the trailer fallback reach selection ──
@pytest.mark.unit
def test_prepare_selects_from_the_code_reviewer_roster_providers() -> None:
    params = prepare_review_params({"repo": "Runner_Dashboard", "pr": 5}, roster=_roster("gemini", "claude"))

    assert params["role"] == "code-reviewer"
    assert params["provider"] == "gemini"


@pytest.mark.unit
def test_prepare_falls_back_to_the_agent_id_commit_trailer(store: RunStore) -> None:
    trailers = _Trailers(["feat: x\n\nAgent-Id: claude\nIssue: #5"])

    params = prepare_review_params(
        {"repo": "Runner_Dashboard", "pr": 5},
        roster=_roster("claude", "codex"),
        store=store,
        gh_probe=trailers,
    )

    assert trailers.calls == [("Runner_Dashboard", 5)]
    assert params["provider"] == "codex"
    assert not is_same_provider_review(params["prompt"])


# ── defect 3: the same-provider mark travels with the run into its outcome ───
@pytest.mark.unit
def test_same_provider_selection_tags_the_prompt_and_the_outcome() -> None:
    params = prepare_review_params(
        {"repo": "Runner_Dashboard", "pr": 9},
        roster=_roster("claude"),
        gh_probe=_Trailers(["Agent-Id: claude"]),
    )

    assert params["provider"] == "claude" and params["model"]
    assert SAME_PROVIDER_TAG in params["prompt"]
    assert is_same_provider_review(params["prompt"])
    line = "STAFF_RESULT: review approve #9"
    assert parse_outcome(line, same_provider=is_same_provider_review(params["prompt"])) == (
        "review approve #9 (same-provider)"
    )


# ── defect 4: auto-review submits through the runner, deduplicates and logs ──
class _Runner:
    def __init__(self, store: RunStore, roster: dict[str, Any], error: Exception | None = None) -> None:
        self.store = store
        self._roster = roster
        self._error = error
        self.submitted: list[RunRequest] = []

    def roles(self) -> dict[str, Any]:
        return self._roster

    def submit(self, req: RunRequest) -> RunRecord:
        if self._error is not None:
            raise self._error
        self.submitted.append(req)
        return _run("run-review-new", role=req.role, provider=req.provider or "")


def _verified(pr: int) -> Any:
    return SimpleNamespace(verification="verified", pr_number=pr)


@pytest.fixture
def auto_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_AUTO_REVIEW", "1")


@pytest.mark.unit
def test_auto_review_submits_a_cross_provider_review_through_the_runner(
    store: RunStore, auto_on: None, caplog: pytest.LogCaptureFixture
) -> None:
    author = _add(store, _run("run-author", provider="codex", thread_id="thread-1"), pr_number=42)
    runner = _Runner(store, _roster("codex", "gemini"))

    with caplog.at_level(logging.INFO, logger="dashboard.staff.review"):
        assert auto_review_if_eligible(author, _verified(42), store=store, runner=runner, gh_probe=_Trailers([]))

    (req,) = runner.submitted
    assert (req.role, req.repo, req.pr, req.provider) == ("code-reviewer", "Runner_Dashboard", 42, "gemini")
    assert req.thread_id == "thread-1" and req.requested_by
    assert "auto-review" in caplog.text and "#42" in caplog.text


@pytest.mark.unit
@pytest.mark.parametrize(
    ("setting", "role", "repo", "verification"),
    [
        ("0", "pragmatic-programmer", "Runner_Dashboard", "verified"),  # opt-in is off
        ("1", "pragmatic-programmer", "Random_Unranked_Repo", "verified"),  # not a P0/P1 repo
        ("1", "code-reviewer", "Runner_Dashboard", "verified"),  # never review a review
        ("1", "pragmatic-programmer", "Runner_Dashboard", "unverified"),  # PR not verified
    ],
)
def test_auto_review_eligibility(
    store: RunStore, monkeypatch: pytest.MonkeyPatch, setting: str, role: str, repo: str, verification: str
) -> None:
    monkeypatch.setenv("STAFF_AUTO_REVIEW", setting)
    author = _add(store, _run("run-author", role=role, repo=repo), pr_number=42)
    runner = _Runner(store, _roster("gemini"))
    verdict = SimpleNamespace(verification=verification, pr_number=42)

    assert auto_review_if_eligible(author, verdict, store=store, runner=runner) is False
    assert runner.submitted == []


@pytest.mark.unit
def test_auto_review_skips_a_pr_that_already_has_a_review_run(store: RunStore, auto_on: None) -> None:
    author = _add(store, _run("run-author"), pr_number=42)
    _add(store, _run("run-old-review", role="code-reviewer", target_kind="pr", target_ref="PR #42"))
    runner = _Runner(store, _roster("gemini"))

    assert auto_review_if_eligible(author, _verified(42), store=store, runner=runner) is False
    assert runner.submitted == []


@pytest.mark.unit
def test_auto_review_reports_a_runner_refusal_as_not_dispatched(
    store: RunStore, auto_on: None, caplog: pytest.LogCaptureFixture
) -> None:
    author = _add(store, _run("run-author"), pr_number=42)
    runner = _Runner(store, _roster("gemini"), error=ValueError("provider gemini not installed"))

    with caplog.at_level(logging.WARNING, logger="dashboard.staff.review"):
        assert auto_review_if_eligible(author, _verified(42), store=store, runner=runner) is False
    assert "provider gemini not installed" in caplog.text


# ── defect 5 and wiring: execute_review_pr ────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("pr", ["abc", "12a", True, -3])
def test_execute_review_pr_rejects_a_non_numeric_pr(pr: Any) -> None:
    from staff.action_executors import execute_review_pr
    from staff.actions import ActionContext

    res = execute_review_pr({"repo": "Runner_Dashboard", "pr": pr}, ActionContext(caller=None))

    assert res.success is False and res.failure_class == "invalid_params"


@pytest.mark.unit
def test_execute_review_pr_passes_the_roster_store_and_trailer_probe(
    store: RunStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from staff import action_executors, roles
    from staff import runner as runner_mod
    from staff.actions import ActionContext, ActionResult

    _add(store, _run("run-author", provider="gemini"), pr_number=11)
    monkeypatch.setattr(roles, "roles_dir", lambda: tmp_path)
    monkeypatch.setattr(roles, "load_roles", lambda _d: _roster("gemini", "codex"))
    monkeypatch.setattr(runner_mod, "get_runner", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(review, "GhCliCommitProbe", lambda: _Trailers([]))
    sent: list[dict[str, Any]] = []

    def fake_dispatch(params: dict[str, Any], _ctx: Any) -> ActionResult:
        sent.append(params)
        return ActionResult(success=True, run_id="run-x")

    monkeypatch.setattr(action_executors, "execute_staff_dispatch", fake_dispatch)

    res = action_executors.execute_review_pr({"repo": "Runner_Dashboard", "pr": "11"}, ActionContext(caller=None))

    assert res.success
    (params,) = sent
    assert params["pr"] == 11 and params["provider"] == "codex"


# ── #1580 review follow-up: dedupe covers the fallback role and is atomic ─────
@pytest.mark.unit
def test_auto_review_dedupe_counts_the_fleet_critic_fallback_reviewer(store: RunStore, auto_on: None) -> None:
    author = _add(store, _run("run-author"), pr_number=42)
    _add(store, _run("run-old-review", role="fleet-critic", target_kind="pr", target_ref="PR #42"))
    runner = _Runner(store, {"fleet-critic": SimpleNamespace(providers=("gemini",))})

    assert auto_review_if_eligible(author, _verified(42), store=store, runner=runner, gh_probe=_Trailers([])) is False
    assert runner.submitted == []


@pytest.mark.unit
def test_concurrent_auto_reviews_of_one_pr_queue_a_single_review(store: RunStore, auto_on: None) -> None:
    import threading
    import time

    author = _add(store, _run("run-author"), pr_number=42)

    class _SlowRunner(_Runner):
        def submit(self, req: RunRequest) -> RunRecord:
            time.sleep(0.05)  # widen the check-then-submit window
            self.submitted.append(req)
            return _add(
                store,
                _run(f"run-review-{len(self.submitted)}", role=req.role, target_kind="pr", target_ref=f"PR #{req.pr}"),
            )

    runner = _SlowRunner(store, _roster("gemini"))
    threads = [
        threading.Thread(
            target=auto_review_if_eligible,
            args=(author, _verified(42)),
            kwargs={"store": store, "runner": runner, "gh_probe": _Trailers([])},
        )
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(runner.submitted) == 1


def _review_in_subprocess(db_path: str, start_at: float) -> int:
    """Child process: one auto-review attempt against the shared runs DB; returns submits."""
    import os
    import time

    os.environ["STAFF_AUTO_REVIEW"] = "1"
    child_store = RunStore(Path(db_path))
    author = child_store.get_run("run-author")

    class _SlowChildRunner(_Runner):
        def submit(self, req: RunRequest) -> RunRecord:
            time.sleep(0.2)  # hold the check-then-submit window open across processes
            self.submitted.append(req)
            return _add(
                child_store,
                _run(f"run-review-{os.getpid()}", role=req.role, target_kind="pr", target_ref=f"PR #{req.pr}"),
            )

    runner = _SlowChildRunner(child_store, _roster("gemini"))
    time.sleep(max(0.0, start_at - time.time()))
    auto_review_if_eligible(author, _verified(42), store=child_store, runner=runner, gh_probe=_Trailers([]))
    return len(runner.submitted)


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="fork context and fcntl cross-process locks are POSIX-only")
def test_auto_review_dedupe_holds_across_worker_processes(tmp_path: Path) -> None:
    import multiprocessing
    import time

    db = tmp_path / "runs.sqlite3"
    _add(RunStore(db), _run("run-author"), pr_number=42)
    start_at = time.time() + 1.0
    with multiprocessing.get_context("fork").Pool(2) as pool:
        submits = pool.starmap(_review_in_subprocess, [(str(db), start_at), (str(db), start_at)])
    assert sum(submits) == 1
