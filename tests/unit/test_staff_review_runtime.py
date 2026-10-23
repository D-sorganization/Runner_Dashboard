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


_REVIEW_START_BARRIER: Any = None


def _set_review_start_barrier(barrier: Any) -> None:
    """Install the shared barrier in a freshly spawned worker process."""
    global _REVIEW_START_BARRIER
    _REVIEW_START_BARRIER = barrier


def _review_in_subprocess(db_path: str) -> dict[str, Any]:
    """Child process: attempt one auto-review and return storage/lock diagnostics."""
    import fcntl
    import multiprocessing
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
    lock_path = Path(f"{child_store.path}.auto-review.lock")
    checks: list[dict[str, Any]] = []
    original_already_reviewed = review._already_reviewed

    def traced_already_reviewed(store: Any, repo: str, pr_number: int, role: str = review.REVIEWER_ROLE) -> bool:
        already_reviewed = original_already_reviewed(store, repo, pr_number, role)
        with store._lock:
            rows = store._conn.execute(
                "SELECT id, status, retry_of FROM runs WHERE role = ? AND repo = ? AND target_ref = ? ORDER BY id",
                (role, repo, f"PR #{pr_number}"),
            ).fetchall()
        checks.append(
            {
                "module": review._already_reviewed.__module__,
                "db_path": str(store.path),
                "already_reviewed": already_reviewed,
                "rows": [(str(row[0]), str(row[1]), str(row[2])) for row in rows],
            }
        )
        return already_reviewed

    review._already_reviewed = traced_already_reviewed
    if _REVIEW_START_BARRIER is not None:
        _REVIEW_START_BARRIER.wait(timeout=10)
    try:
        queued = auto_review_if_eligible(
            author, _verified(42), store=child_store, runner=runner, gh_probe=_Trailers([])
        )
        with child_store._lock:
            database_path = str(child_store._conn.execute("PRAGMA database_list").fetchone()[2])
            reviews = child_store._conn.execute(
                "SELECT id, status FROM runs WHERE role = ? AND repo = ? AND target_ref = ? ORDER BY id",
                ("code-reviewer", "Runner_Dashboard", "PR #42"),
            ).fetchall()
        lock_stat = lock_path.stat()
        return {
            "pid": os.getpid(),
            "db_path": str(child_store.path),
            "sqlite_path": database_path,
            "lock_identity": (lock_stat.st_dev, lock_stat.st_ino),
            "queued": queued,
            "submit_count": len(runner.submitted),
            "reviews": [(str(row[0]), str(row[1])) for row in reviews],
            "checks": checks,
            "fcntl_module": getattr(fcntl, "__file__", None),
            "flock_type": f"{type(fcntl.flock).__module__}.{type(fcntl.flock).__qualname__}",
            "flock_module": getattr(fcntl.flock, "__module__", None),
            "start_method": multiprocessing.get_start_method(),
            "os_name": os.name,
        }
    finally:
        child_store._conn.close()


def _assert_review_database_is_intact(path: Path) -> None:
    check_store = RunStore(path)
    try:
        integrity = check_store._conn.execute("PRAGMA integrity_check").fetchone()[0]
        reviews = check_store.list_runs_for_target(role="code-reviewer", repo="Runner_Dashboard", target_ref="PR #42")
    finally:
        check_store._conn.close()
    assert integrity == "ok"
    assert len(reviews) == 1


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="fcntl cross-process locks are POSIX-only")
def test_auto_review_dedupe_holds_across_worker_processes(tmp_path: Path) -> None:
    import multiprocessing

    db = tmp_path / "runs.sqlite3"
    seed_store = RunStore(db)
    _add(seed_store, _run("run-author"), pr_number=42)
    seed_store._conn.close()
    context = multiprocessing.get_context("spawn")
    with context.Pool(2, initializer=_set_review_start_barrier, initargs=(context.Barrier(2),)) as pool:
        attempts = pool.map(_review_in_subprocess, [str(db), str(db)])
    assert sum(attempt["submit_count"] for attempt in attempts) == 1, attempts
    assert all(attempt["start_method"] == "spawn" for attempt in attempts), attempts
    _assert_review_database_is_intact(db)


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="fcntl cross-process locks are POSIX-only")
def test_auto_review_dedupe_uses_database_identity_across_path_aliases(tmp_path: Path) -> None:
    import multiprocessing

    db = tmp_path / "runs.sqlite3"
    seed_store = RunStore(db)
    _add(seed_store, _run("run-author"), pr_number=42)
    seed_store._conn.close()
    alias = tmp_path / "runs-alias.sqlite3"
    alias.symlink_to(db)
    context = multiprocessing.get_context("spawn")
    with context.Pool(2, initializer=_set_review_start_barrier, initargs=(context.Barrier(2),)) as pool:
        attempts = pool.map(_review_in_subprocess, [str(db), str(alias)])
    assert sum(attempt["submit_count"] for attempt in attempts) == 1, attempts
    assert len({attempt["pid"] for attempt in attempts}) == 2, attempts
    assert len({attempt["lock_identity"] for attempt in attempts}) == 1, attempts
    assert len({attempt["sqlite_path"] for attempt in attempts}) == 1, attempts
    assert len({attempt["db_path"] for attempt in attempts}) == 1, attempts
    assert all(attempt["flock_module"] == "fcntl" for attempt in attempts), attempts
    assert all(attempt["flock_type"] == "builtins.builtin_function_or_method" for attempt in attempts), attempts
    assert all(attempt["start_method"] == "spawn" and attempt["os_name"] == "posix" for attempt in attempts), attempts
    assert sorted(attempt["queued"] for attempt in attempts) == [False, True], attempts
    assert all(len(attempt["reviews"]) == 1 for attempt in attempts), attempts
    assert attempts[0]["reviews"] == attempts[1]["reviews"], attempts
    assert all(len(attempt["checks"]) == 1 for attempt in attempts), attempts
    assert sorted(attempt["checks"][0]["already_reviewed"] for attempt in attempts) == [False, True], attempts
    assert all(attempt["checks"][0]["db_path"] == attempt["db_path"] for attempt in attempts), attempts
    _assert_review_database_is_intact(db)


@pytest.mark.unit
def test_run_store_canonicalizes_alias_to_not_yet_created_database(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "runs.sqlite3"
    target.parent.mkdir()
    alias = tmp_path / "runs-alias.sqlite3"
    alias.symlink_to(target)

    store = RunStore(alias)
    try:
        assert store.path == target.resolve()
        assert target.is_file()
    finally:
        store.close()


@pytest.mark.unit
def test_auto_review_fails_closed_when_runner_persists_to_another_store(
    tmp_path: Path, auto_on: None, caplog: pytest.LogCaptureFixture
) -> None:
    author_store = RunStore(tmp_path / "verification.sqlite3")
    runner_store = RunStore(tmp_path / "runner.sqlite3")
    author = _add(author_store, _run("run-author"), pr_number=42)
    runner = _Runner(runner_store, _roster("gemini"))

    with caplog.at_level(logging.WARNING, logger="dashboard.staff.review"):
        queued = auto_review_if_eligible(
            author, _verified(42), store=author_store, runner=runner, gh_probe=_Trailers([])
        )

    assert queued is False
    assert runner.submitted == []
    assert "store identity mismatch" in caplog.text


@pytest.mark.unit
def test_auto_review_allows_distinct_store_handles_for_same_database(tmp_path: Path, auto_on: None) -> None:
    db = tmp_path / "runs.sqlite3"
    alias = tmp_path / "runs-alias.sqlite3"
    alias.symlink_to(db)
    verification_store = RunStore(db)
    runner_store = RunStore(alias)
    author = _add(verification_store, _run("run-author"), pr_number=42)
    runner = _Runner(runner_store, _roster("gemini"))

    assert (
        auto_review_if_eligible(author, _verified(42), store=verification_store, runner=runner, gh_probe=_Trailers([]))
        is True
    )
    assert len(runner.submitted) == 1
