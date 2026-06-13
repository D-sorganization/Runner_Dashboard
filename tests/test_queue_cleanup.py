"""Tests for backend/queue_cleanup.py — issue #386."""

from __future__ import annotations

import asyncio
import datetime as _dt

import queue_cleanup as qc

UTC = getattr(_dt, "UTC", _dt.UTC)


# ---------------------------------------------------------------------------
# StaleRun dataclass
# ---------------------------------------------------------------------------


def test_stale_run_as_dict() -> None:
    run = qc.StaleRun(
        repo="my-repo",
        run_id=12345,
        workflow="CI",
        branch="main",
        created_at="2026-04-01T10:00:00Z",
        age_minutes=90,
    )
    d = run.as_dict()
    assert d["repo"] == "my-repo"
    assert d["run_id"] == 12345
    assert d["age_minutes"] == 90
    assert d["cancelled"] is False


def test_stale_run_cancelled_field() -> None:
    run = qc.StaleRun(
        repo="r",
        run_id=1,
        workflow="w",
        branch="b",
        created_at="2026-04-01T10:00:00Z",
        age_minutes=120,
        cancelled=True,
        cancel_error="",
    )
    assert run.cancelled is True


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_default_min_age_minutes_reasonable() -> None:
    assert qc.DEFAULT_MIN_AGE_MINUTES > 0


def test_scan_concurrency_positive() -> None:
    assert qc._SCAN_CONCURRENCY > 0


# ---------------------------------------------------------------------------
# classify_stale_run — anchored agent classification (issue #934)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

_ABANDONED = qc.StaleReason.ABANDONED_AGENT.value
_STALE_FEATURE = qc.StaleReason.STALE_FEATURE_BRANCH.value


@pytest.mark.parametrize(
    "branch",
    [
        "fix/rerun-tests",  # used to match "run-"
        "feat/dispatch-fix",  # used to match "patch-"
        "feature/user-agent-header",  # used to match "agent"
        "bugfix/patch-notes",  # "patch-" mid-segment
    ],
)
def test_human_feature_branches_not_classified_agent(branch: str) -> None:
    """#934: ordinary human branches must NOT be abandoned-agent / safe_to_cancel
    via substring matching."""
    reason, safe = qc.classify_stale_run(branch, age_minutes=120, actor="alice")
    assert reason == _STALE_FEATURE
    # stale-feature-branch is still cancellable by age, but it is NOT flagged as
    # an abandoned agent run (the dangerous reaper class).
    assert reason != _ABANDONED


@pytest.mark.parametrize(
    "branch",
    ["agent/foo", "codex/bar", "jules/baz", "wt-123", "worktree/x", "patch-1", "run-7"],
)
def test_agent_branches_with_bot_actor_are_abandoned_agent(branch: str) -> None:
    """#934: agent-shaped branches from a bot actor ARE abandoned-agent runs."""
    reason, safe = qc.classify_stale_run(branch, age_minutes=120, actor="codex[bot]")
    assert reason == _ABANDONED
    assert safe is True


def test_agent_branch_human_actor_requires_corroboration() -> None:
    """#934: a human-actor run on an agent-named branch is NOT abandoned-agent."""
    reason, safe = qc.classify_stale_run("agent/foo", age_minutes=120, actor="alice")
    assert reason == _STALE_FEATURE


def test_agent_branch_unknown_actor_preserves_branch_only_behaviour() -> None:
    """When the actor is unknown, branch shape alone decides (back-compat)."""
    reason, safe = qc.classify_stale_run("agent/foo", age_minutes=120)
    assert reason == _ABANDONED
    assert safe is True


def test_named_bot_actors_recognized() -> None:
    for actor in ("jules", "dashboard-bot", "github-actions", "somebody[bot]"):
        assert qc._is_agent_actor(actor) is True
    for actor in ("alice", "", None):
        assert qc._is_agent_actor(actor) is False


def test_main_branch_classification_unchanged() -> None:
    assert qc.classify_stale_run("main", age_minutes=400)[1] is True
    assert qc.classify_stale_run("main", age_minutes=100) == (qc.StaleReason.STALE_MAIN_BRANCH.value, False)


def test_cancel_concurrency_positive() -> None:
    assert qc._CANCEL_CONCURRENCY > 0


# ---------------------------------------------------------------------------
# PR-head supersession classifier
# ---------------------------------------------------------------------------


def test_pr_head_supersession_requires_pr_event(monkeypatch) -> None:
    async def fake_gh_json(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("non-PR runs must not query PR state")

    monkeypatch.setattr(qc, "_gh_json", fake_gh_json)

    result = asyncio.run(
        qc.classify_pr_head_supersession(
            "org",
            "repo",
            {"event": "push", "head_sha": "old", "pull_requests": [{"number": 1}]},
        )
    )

    assert result["pr_head_superseded"] is False
    assert result["supersession_reason"] == "not-pr-run"


def test_pr_head_supersession_detects_open_pr_head_advanced(monkeypatch) -> None:
    async def fake_gh_json(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return {"state": "open", "head": {"sha": "new-sha"}}

    monkeypatch.setattr(qc, "_gh_json", fake_gh_json)

    result = asyncio.run(
        qc.classify_pr_head_supersession(
            "org",
            "repo",
            {
                "event": "pull_request",
                "head_sha": "old-sha",
                "pull_requests": [{"number": 123}],
            },
        )
    )

    assert result["pull_request_number"] == 123
    assert result["current_pr_head_sha"] == "new-sha"
    assert result["pr_head_superseded"] is True
    assert result["supersession_reason"] == "pr-head-advanced"


def test_pr_head_supersession_is_false_for_current_head(monkeypatch) -> None:
    async def fake_gh_json(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return {"state": "open", "head": {"sha": "same-sha"}}

    monkeypatch.setattr(qc, "_gh_json", fake_gh_json)

    result = asyncio.run(
        qc.classify_pr_head_supersession(
            "org",
            "repo",
            {
                "event": "pull_request",
                "head_sha": "same-sha",
                "pull_requests": [{"number": 123}],
            },
        )
    )

    assert result["pr_head_superseded"] is False
    assert result["supersession_reason"] == "current-pr-head"


def test_pr_head_supersession_rejects_ambiguous_pr_evidence(monkeypatch) -> None:
    async def fake_gh_json(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("ambiguous PR evidence must not query PR state")

    monkeypatch.setattr(qc, "_gh_json", fake_gh_json)

    result = asyncio.run(
        qc.classify_pr_head_supersession(
            "org",
            "repo",
            {
                "event": "pull_request",
                "head_sha": "old-sha",
                "pull_requests": [{"number": 1}, {"number": 2}],
            },
        )
    )

    assert result["pr_head_superseded"] is False
    assert result["supersession_reason"] == "ambiguous-pr"


def test_purge_stale_runs_can_limit_to_superseded(monkeypatch) -> None:
    stale_runs = [
        qc.StaleRun("repo", 1, "CI", "branch", "2026-04-01T10:00:00Z", 120),
        qc.StaleRun(
            "repo",
            2,
            "CI",
            "branch",
            "2026-04-01T10:00:00Z",
            120,
            pr_head_superseded=True,
        ),
    ]
    cancelled: list[int] = []

    async def fake_find_stale_runs(_org: str, _min_age_minutes: int, *args, **kwargs) -> list[qc.StaleRun]:
        return stale_runs

    async def fake_cancel_one(_org: str, run: qc.StaleRun) -> bool:
        cancelled.append(run.run_id)
        run.cancelled = True
        return True

    monkeypatch.setattr(qc, "find_stale_runs", fake_find_stale_runs)
    monkeypatch.setattr(qc, "_cancel_one", fake_cancel_one)

    result = asyncio.run(qc.purge_stale_runs("org", superseded_only=True))

    assert result["stale_count"] == 2
    assert result["purge_candidate_count"] == 1
    assert result["cancelled_count"] == 1
    assert cancelled == [2]
