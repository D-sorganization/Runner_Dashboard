"""#4702: the job-board open-issues listing is cached and budget-bounded.

The job board shells out to ``gh api --paginate`` for the full open-issues list
on every request. That single call can consume most of the coordination response
budget and turn an otherwise-usable listing into a bare 503. These tests pin the
behaviour that a fresh listing is reused, and that a slow upstream fetch serves
the last-known list as incomplete rather than blocking or returning nothing.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from coordination import gh_cli
from coordination import jobs as jobs_mod


@pytest.fixture(autouse=True)
def _reset_issue_list_cache() -> None:
    import importlib

    try:
        issue_list_cache = importlib.import_module("coordination.issue_list_cache")
    except ImportError:
        issue_list_cache = None
    if issue_list_cache is not None:
        issue_list_cache.reset()
    yield
    if issue_list_cache is not None:
        issue_list_cache.reset()


def _issue(number: int) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"issue {number}",
        "labels": [],
        "created_at": "2026-01-01T00:00:00Z",
        "html_url": f"https://example.test/{number}",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def test_candidates_reuse_cached_issue_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second job-board listing must not re-fetch the open-issues list from GitHub."""
    calls: list[str] = []

    def fake_paginate(path: str) -> list[Any]:
        calls.append(path)
        return [_issue(1)]

    monkeypatch.setattr(gh_cli, "gh_api_json_all", fake_paginate)

    first, complete_first = jobs_mod._candidates("Tools", [])
    second, complete_second = jobs_mod._candidates("Tools", [])

    assert complete_first is True
    assert complete_second is True
    assert first == second
    assert len(calls) == 1


def test_candidates_serve_stale_cache_when_fetch_misses_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow open-issues fetch serves the last-known list with ``complete=False``."""
    from coordination import issue_list_cache

    monkeypatch.setattr(issue_list_cache, "_ttl_seconds", lambda: 0.0)
    monkeypatch.setattr(issue_list_cache, "_budget_seconds", lambda: 0.05)
    monkeypatch.setattr(gh_cli, "gh_api_json_all", lambda path: [_issue(1)])

    first, complete_first = jobs_mod._candidates("Tools", [])
    assert complete_first is True
    assert first == [_issue(1)]

    gate = threading.Event()

    def slow(path: str) -> list[Any]:
        gate.wait(1)
        return [_issue(2)]

    monkeypatch.setattr(gh_cli, "gh_api_json_all", slow)
    started = time.monotonic()
    try:
        issues, complete = jobs_mod._candidates("Tools", [])
    finally:
        gate.set()

    assert time.monotonic() - started < 0.5
    assert complete is False
    assert issues == [_issue(1)]


def test_list_jobs_marks_incomplete_when_issue_list_served_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """``list_jobs`` reports ``complete=False`` (not an empty board) on a stale issue list."""
    from coordination import issue_list_cache

    monkeypatch.setattr(issue_list_cache, "_ttl_seconds", lambda: 0.0)
    monkeypatch.setattr(issue_list_cache, "_budget_seconds", lambda: 0.05)
    monkeypatch.setattr(jobs_mod, "_safe_top_priorities", lambda _limit: ([], []))
    monkeypatch.setattr(jobs_mod, "repo_schedule_holds", lambda _repo: ([], True))
    monkeypatch.setattr(jobs_mod, "_closing_pr", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jobs_mod.claims_mod, "check", lambda _repo, _n: {"available": True, "held": False})

    monkeypatch.setattr(gh_cli, "gh_api_json_all", lambda path: [_issue(1)])
    jobs_mod.list_jobs(repo="Tools", limit=10, include_excluded=False)

    gate = threading.Event()

    def slow(path: str) -> list[Any]:
        gate.wait(1)
        return [_issue(2)]

    monkeypatch.setattr(gh_cli, "gh_api_json_all", slow)
    try:
        result = jobs_mod.list_jobs(repo="Tools", limit=10, include_excluded=False)
    finally:
        gate.set()

    assert result["complete"] is False
    assert [job["issue"] for job in result["jobs"]] == [1]
    assert any("cache" in warning or "budget" in warning for warning in result["warnings"])
