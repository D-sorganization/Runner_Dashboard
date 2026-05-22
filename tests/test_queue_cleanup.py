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

    async def fake_find_stale_runs(_org: str, _min_age_minutes: int) -> list[qc.StaleRun]:
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
