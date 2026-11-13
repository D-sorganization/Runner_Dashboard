"""Hermetic tests for kanban GitHub ingestion (#4319)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import gh_client
import kanban_github_ingestion as kgi
import pytest
from kanban_projection import KanbanColumn, project_kanban_column, snapshot_from_mapping


class FakeGitHub:
    """Records GET paths and serves multi-page issue lists."""

    def __init__(
        self,
        *,
        issue_pages: list[list[dict]] | None = None,
        gets: dict[str, Any] | None = None,
        rate_limit_at: int | None = None,
    ) -> None:
        self.issue_pages = issue_pages or []
        self.gets = gets or {}
        self.calls = 0
        self.get_paths: list[str] = []
        self._rate_limit_at = rate_limit_at
        self._issue_page_idx = 0

    async def get(self, path: str) -> Any:
        self.calls += 1
        self.get_paths.append(path)
        if self._rate_limit_at is not None and self.calls >= self._rate_limit_at:
            raise kgi.KanbanGithubSourceError("rate_limited", "primary rate limit", path)
        if path not in self.gets:
            raise kgi.KanbanGithubSourceError("missing", f"no fixture for {path}", path)
        return self.gets[path]

    async def list_page(self, url: str) -> tuple[list[dict], str | None]:
        if self._issue_page_idx >= len(self.issue_pages):
            return [], None
        page = self.issue_pages[self._issue_page_idx]
        self._issue_page_idx += 1
        self.calls += 1
        next_url = (
            f"/repos/o/r/issues?state=open&per_page=100&page={self._issue_page_idx + 1}"
            if self._issue_page_idx < len(self.issue_pages)
            else None
        )
        return page, next_url


def _issue(number: int, *, labels: list[str] | None = None) -> dict:
    return {
        "number": number,
        "state": "open",
        "html_url": f"https://github.com/o/r/issues/{number}",
        "labels": [{"name": n} for n in (labels or [])],
        "body": "",
    }


def _pull(number: int, *, head: str, body: str = "", draft: bool = False) -> dict:
    return {
        "number": number,
        "state": "open",
        "draft": draft,
        "html_url": f"https://github.com/o/r/pull/{number}",
        "body": body,
        "head": {"sha": head},
        "requested_reviewers": [{"login": "diete"}],
        "auto_merge": None,
    }


@pytest.fixture(autouse=True)
def _reset_kanban_github_state(monkeypatch: pytest.MonkeyPatch) -> None:
    kgi.reset_kanban_github_cache()
    monkeypatch.setattr(kgi, "KANBAN_GITHUB_COLD_BUDGET", 40)


def _sync(fake: FakeGitHub) -> kgi.KanbanGithubSyncResult:
    return asyncio.run(
        kgi.sync_repo_github_kanban(
            "o",
            "r",
            github_get=fake.get,
            list_page_fetcher=fake.list_page,
            now=lambda: datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
        )
    )


def test_paginate_issues_follows_three_pages() -> None:
    """List calls paginate; three pages of issues are merged (AC #4319)."""
    page_size = 100
    pages = [
        [_issue(i) for i in range(1, page_size + 1)],
        [_issue(i) for i in range(page_size + 1, 2 * page_size + 1)],
        [_issue(i) for i in range(2 * page_size + 1, 2 * page_size + 11)],
    ]
    fake = FakeGitHub(
        issue_pages=pages,
        gets={"/repos/o/r/pulls?state=open&per_page=100": []},
    )
    result = _sync(fake)
    assert result.metrics.github_api_calls == 4  # 3 issue pages + 1 pull list get
    assert len(result.items) == 210
    assert result.freshness["issues"].status == "ok"


def test_cold_sync_respects_api_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kgi, "KANBAN_GITHUB_COLD_BUDGET", 2)
    fake = FakeGitHub(
        issue_pages=[[_issue(1)], [_issue(2)], [_issue(3)]],
        gets={"/repos/o/r/pulls?state=open&per_page=100": []},
    )
    result = _sync(fake)
    assert result.metrics.github_api_calls <= 2
    assert result.metrics.api_budget_exhausted
    assert result.freshness["issues"].status == "degraded"


def test_warm_sync_uses_cache_with_zero_api_calls() -> None:
    fake = FakeGitHub(
        issue_pages=[[_issue(1)]],
        gets={"/repos/o/r/pulls?state=open&per_page=100": []},
    )
    first = _sync(fake)
    assert first.sync_mode == "cold"
    assert first.metrics.github_api_calls >= 1
    calls_after_cold = fake.calls
    second = _sync(fake)
    assert second.sync_mode == "warm"
    assert second.metrics.github_api_calls == 0
    assert second.metrics.cache_hits >= 1
    assert fake.calls == calls_after_cold


def test_stale_review_surfaces_on_card() -> None:
    body = "Closes #9"
    fake = FakeGitHub(
        issue_pages=[[_issue(9)]],
        gets={
            "/repos/o/r/pulls?state=open&per_page=100": [_pull(50, head="head999", body=body)],
            "/repos/o/r/pulls/50": _pull(50, head="head999", body=body),
            "/repos/o/r/commits/head999/check-runs?per_page=100": {"check_runs": []},
            "/repos/o/r/pulls/50/reviews": [
                {
                    "id": 1,
                    "user": {"login": "bot"},
                    "state": "APPROVED",
                    "commit_id": "old888",
                    "html_url": "https://github.com/o/r/pull/50#review_old",
                }
            ],
            "/repos/o/r/issues/50/timeline?per_page=30": [],
        },
    )
    result = _sync(fake)
    row = next(i for i in result.items if i.issue_number == 9)
    card = project_kanban_column(snapshot_from_mapping(row.snapshot))
    assert card.column == KanbanColumn.REVIEW
    assert len(card.stale_reviews) == 1
    assert card.stale_reviews[0].commit_id == "old888"


def test_rate_limited_source_yields_unknown_not_stale_guess() -> None:
    fake = FakeGitHub(
        issue_pages=[[_issue(1)]],
        gets={
            "/repos/o/r/pulls?state=open&per_page=100": [_pull(2, head="abc", body="Closes #1")],
            "/repos/o/r/pulls/2": _pull(2, head="abc", body="Closes #1"),
        },
        rate_limit_at=4,
    )
    result = _sync(fake)
    row = next(i for i in result.items if i.issue_number == 1)
    card = project_kanban_column(snapshot_from_mapping(row.snapshot))
    assert card.column == KanbanColumn.UNKNOWN
    assert result.freshness["pull_requests"].status == "degraded"


def test_sync_metrics_contract() -> None:
    fake = FakeGitHub(
        issue_pages=[[_issue(1)]],
        gets={"/repos/o/r/pulls?state=open&per_page=100": []},
    )
    result = _sync(fake)
    assert result.last_sync.endswith("Z") or "+" in result.last_sync
    assert result.cache_ttl_seconds == kgi.KANBAN_GITHUB_CACHE_TTL_SECONDS
    assert "issues" in result.freshness
    assert result.metrics.cache_misses >= 1


def test_budget_guard_raises_in_tests_when_exceeded() -> None:
    with pytest.raises(AssertionError, match="budget"):
        with kgi.github_sync_budget_guard(max_calls=0):
            kgi.record_github_api_call()


def _merge_queue_timeline() -> list[dict]:
    return [{"event": "added_to_merge_queue"}]


def _armed_pull_fixture() -> dict[str, Any]:
    body = "Closes #9"
    return {
        "/repos/o/r/pulls?state=open&per_page=100": [_pull(50, head="queue_head", body=body)],
        "/repos/o/r/pulls/50": _pull(50, head="queue_head", body=body),
        "/repos/o/r/commits/queue_head/check-runs?per_page=100": {
            "check_runs": [{"status": "completed", "conclusion": "success"}],
        },
        "/repos/o/r/pulls/50/reviews": [],
        "/repos/o/r/issues/50/timeline?per_page=30": _merge_queue_timeline(),
    }


@pytest.mark.parametrize(
    ("exc_factory",),
    [
        (lambda path: gh_client.GhRateLimited(retry_after_seconds=30, endpoint=path),),
        (lambda path: gh_client.GhServerError(502, path, "boom"),),
        (lambda path: gh_client.GhClientError("timeout", kind="timeout", attempts=3),),
    ],
)
def test_gh_client_errors_on_issues_list_yield_degraded_sync(exc_factory) -> None:
    class GhErrorListPage:
        def __init__(self, exc: BaseException) -> None:
            self.exc = exc
            self.calls = 0

        async def list_page(self, url: str) -> tuple[list[dict], str | None]:
            self.calls += 1
            raise self.exc

        async def get(self, path: str) -> Any:
            if path == "/repos/o/r/pulls?state=open&per_page=100":
                return []
            raise AssertionError(f"unexpected get {path}")

    exc = exc_factory("/repos/o/r/issues?state=open")
    fetcher = GhErrorListPage(exc)
    result = asyncio.run(
        kgi.sync_repo_github_kanban(
            "o",
            "r",
            github_get=fetcher.get,
            list_page_fetcher=fetcher.list_page,
            now=lambda: datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
        )
    )
    assert result.freshness["issues"].status == "degraded"
    assert result.items == ()


@pytest.mark.parametrize(
    ("exc_factory",),
    [
        (lambda path: gh_client.GhRateLimited(retry_after_seconds=30, endpoint=path),),
        (lambda path: gh_client.GhServerError(502, path, "boom"),),
        (lambda path: gh_client.GhClientError("timeout", kind="timeout", attempts=3),),
    ],
)
def test_gh_client_errors_on_open_pulls_list_yield_degraded_sync(exc_factory) -> None:
    pulls_path = "/repos/o/r/pulls?state=open&per_page=100"

    async def get(path: str) -> Any:
        if path == pulls_path:
            raise exc_factory(path)
        raise AssertionError(f"unexpected get {path}")

    fake = FakeGitHub(issue_pages=[[_issue(1)]], gets={})
    result = asyncio.run(
        kgi.sync_repo_github_kanban(
            "o",
            "r",
            github_get=get,
            list_page_fetcher=fake.list_page,
            now=lambda: datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
        )
    )
    assert result.freshness["pull_requests"].status == "degraded"
    row = next(i for i in result.items if i.issue_number == 1)
    card = project_kanban_column(snapshot_from_mapping(row.snapshot))
    assert card.column == KanbanColumn.UNKNOWN


@pytest.mark.parametrize(
    ("exc_factory",),
    [
        (lambda path: gh_client.GhRateLimited(retry_after_seconds=30, endpoint=path),),
        (lambda path: gh_client.GhServerError(502, path, "boom"),),
        (lambda path: gh_client.GhClientError("timeout", kind="timeout", attempts=3),),
    ],
)
def test_gh_client_errors_during_pull_enrich_yield_unknown_card(exc_factory) -> None:
    body = "Closes #1"
    detail_path = "/repos/o/r/pulls/2"
    backing = FakeGitHub(
        issue_pages=[[_issue(1)]],
        gets={
            "/repos/o/r/pulls?state=open&per_page=100": [_pull(2, head="abc", body=body)],
            detail_path: _pull(2, head="abc", body=body),
        },
    )

    async def get(path: str) -> Any:
        if path == detail_path:
            raise exc_factory(path)
        return await backing.get(path)

    list_fake = FakeGitHub(
        issue_pages=[[_issue(1)]],
        gets={
            "/repos/o/r/pulls?state=open&per_page=100": [_pull(2, head="abc", body=body)],
        },
    )
    result = asyncio.run(
        kgi.sync_repo_github_kanban(
            "o",
            "r",
            github_get=get,
            list_page_fetcher=list_fake.list_page,
            now=lambda: datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
        )
    )
    assert result.freshness["pull_requests"].status == "degraded"
    row = next(i for i in result.items if i.issue_number == 1)
    card = project_kanban_column(snapshot_from_mapping(row.snapshot))
    assert card.column == KanbanColumn.UNKNOWN
    assert row.snapshot["pull_request"]["available"] is False


def test_failed_merge_queue_timeline_marks_degraded_not_false_in_queue() -> None:
    gets = _armed_pull_fixture()
    list_fake = FakeGitHub(issue_pages=[[_issue(9)]], gets=gets)

    async def get_ok(path: str) -> Any:
        return await list_fake.get(path)

    ok = asyncio.run(
        kgi.sync_repo_github_kanban(
            "o",
            "r",
            github_get=get_ok,
            list_page_fetcher=list_fake.list_page,
            now=lambda: datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
        )
    )
    kgi.reset_kanban_github_cache()
    row_ok = next(i for i in ok.items if i.issue_number == 9)
    assert project_kanban_column(snapshot_from_mapping(row_ok.snapshot)).column == KanbanColumn.ARMED

    timeline_path = "/repos/o/r/issues/50/timeline?per_page=30"
    kgi.reset_kanban_github_cache()
    list_fake_retry = FakeGitHub(issue_pages=[[_issue(9)]], gets=gets)

    async def get_timeline_fail(path: str) -> Any:
        if path == timeline_path:
            raise gh_client.GhServerError(502, path, "timeline down")
        return await list_fake_retry.get(path)

    bad = asyncio.run(
        kgi.sync_repo_github_kanban(
            "o",
            "r",
            github_get=get_timeline_fail,
            list_page_fetcher=list_fake_retry.list_page,
            now=lambda: datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
            force_cold=True,
        )
    )
    row_bad = next(i for i in bad.items if i.issue_number == 9)
    assert bad.freshness["pull_requests"].status == "degraded"
    assert row_bad.snapshot["pull_request"]["degraded"] is True
    assert row_bad.snapshot["pull_request"]["in_merge_queue"] is False
    card = project_kanban_column(snapshot_from_mapping(row_bad.snapshot))
    assert card.column == KanbanColumn.UNKNOWN
