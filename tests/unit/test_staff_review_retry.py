"""Auto-review retry after a failed review (#1662), and the review claim's lock file.

Split from ``test_staff_review_runtime.py`` to keep that module under the 400-line
limit; the fixtures below and the helpers imported from it are the same.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.review import RUN_LOOKUP_LIMIT, auto_review_if_eligible
from staff.store import RunStore
from test_staff_review_runtime import _add, _roster, _run, _Runner, _Trailers, _verified


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "runs.sqlite3")


@pytest.fixture
def auto_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_AUTO_REVIEW", "1")


@pytest.mark.unit
def test_review_claim_writes_no_lock_file_for_a_store_without_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    from staff.review import _review_claim

    monkeypatch.chdir(tmp_path)
    with _review_claim(MagicMock()):
        pass
    assert list(tmp_path.glob("*.auto-review.lock")) == []


@pytest.mark.unit
def test_auto_review_retries_a_pr_whose_only_review_failed(store: RunStore, auto_on: None) -> None:
    author = _add(store, _run("run-author"), pr_number=42)
    _add(store, _run("run-failed-review", role="code-reviewer", target_kind="pr", target_ref="PR #42", status="failed"))
    runner = _Runner(store, _roster("gemini"))

    assert auto_review_if_eligible(author, _verified(42), store=store, runner=runner, gh_probe=_Trailers([])) is True
    assert len(runner.submitted) == 1


@pytest.mark.unit
def test_auto_review_stops_after_two_failed_review_attempts(store: RunStore, auto_on: None) -> None:
    author = _add(store, _run("run-author"), pr_number=42)
    for n in (1, 2):
        _add(
            store, _run(f"run-failed-{n}", role="code-reviewer", target_kind="pr", target_ref="PR #42", status="failed")
        )
    runner = _Runner(store, _roster("gemini"))

    assert auto_review_if_eligible(author, _verified(42), store=store, runner=runner, gh_probe=_Trailers([])) is False
    assert runner.submitted == []


@pytest.mark.unit
@pytest.mark.parametrize("status", ["cancelled", "needs_input", "succeeded"])
def test_auto_review_does_not_retry_a_review_that_did_not_fail(store: RunStore, auto_on: None, status: str) -> None:
    author = _add(store, _run("run-author"), pr_number=42)
    _add(store, _run("run-old-review", role="code-reviewer", target_kind="pr", target_ref="PR #42", status=status))
    runner = _Runner(store, _roster("gemini"))

    assert auto_review_if_eligible(author, _verified(42), store=store, runner=runner, gh_probe=_Trailers([])) is False
    assert runner.submitted == []


@pytest.mark.unit
def test_auto_review_waits_for_the_runner_retry_of_a_failed_review(store: RunStore, auto_on: None) -> None:
    author = _add(store, _run("run-author"), pr_number=42)
    _add(store, _run("run-review", role="code-reviewer", target_kind="pr", target_ref="PR #42", status="failed"))
    _add(
        store,
        _run(
            "run-review-retry",
            role="code-reviewer",
            target_kind="pr",
            target_ref="PR #42",
            retry_of="run-review",
            attempt=2,
        ),
    )
    runner = _Runner(store, _roster("gemini"))

    assert auto_review_if_eligible(author, _verified(42), store=store, runner=runner, gh_probe=_Trailers([])) is False
    assert runner.submitted == []


@pytest.mark.unit
def test_attempt_cap_holds_beyond_the_recent_run_window(store: RunStore, auto_on: None) -> None:
    """Newer reviews of other PRs must not push this PR's failed attempts out of view."""
    author = _add(store, _run("run-author"), pr_number=42)
    for n in (1, 2):
        _add(
            store, _run(f"run-failed-{n}", role="code-reviewer", target_kind="pr", target_ref="PR #42", status="failed")
        )
    for n in range(RUN_LOOKUP_LIMIT + 5):
        _add(store, _run(f"run-other-{n}", role="code-reviewer", target_kind="pr", target_ref=f"PR #{1000 + n}"))
    runner = _Runner(store, _roster("gemini"))

    assert auto_review_if_eligible(author, _verified(42), store=store, runner=runner, gh_probe=_Trailers([])) is False
    assert runner.submitted == []
