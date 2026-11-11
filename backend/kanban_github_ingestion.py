"""Bounded, cached GitHub reads for kanban board projection (#4319, epic #4317).

Assembles the GitHub slices of :class:`~kanban_projection.KanbanWorkItemSnapshot`
(issue, pull request, checks, reviews, auto-merge). No card storage; no writes.
Claims, holds, and staff runs are left to #4320–#4321.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from cache_utils import Cache
from kanban_projection import (
    KanbanAutoMergeSnapshot,
    KanbanCard,
    KanbanChecksSnapshot,
    KanbanHeadReview,
    KanbanIssueSnapshot,
    KanbanPullRequestSnapshot,
    KanbanReviewsSnapshot,
    project_kanban_column,
    snapshot_from_mapping,
)
from staff.verification_types import ci_state

log = logging.getLogger("dashboard.kanban_github")

KANBAN_GITHUB_CACHE_TTL_SECONDS = float(os.environ.get("KANBAN_GITHUB_CACHE_TTL_SECONDS", "60"))
KANBAN_GITHUB_COLD_BUDGET = int(os.environ.get("KANBAN_GITHUB_COLD_BUDGET", "40"))
KANBAN_GITHUB_WARM_BUDGET = int(os.environ.get("KANBAN_GITHUB_WARM_BUDGET", "5"))

_CLOSING = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)

_cache = Cache(name="kanban_github", deepcopy_on_set=True)

_test_budget_limit: ContextVar[int | None] = ContextVar("kanban_github_budget_limit", default=None)
_test_budget_calls: ContextVar[int | None] = ContextVar("kanban_github_budget_calls", default=None)


class KanbanGithubSourceError(RuntimeError):
    """GitHub read failed for one kanban source."""

    def __init__(self, kind: str, detail: str, endpoint: str = "") -> None:
        super().__init__(detail)
        self.kind = kind
        self.endpoint = endpoint


@dataclass(frozen=True, slots=True)
class SourceFreshness:
    status: Literal["ok", "degraded", "cached"]
    synced_at: str | None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class KanbanGithubMetrics:
    github_api_calls: int
    cache_hits: int
    cache_misses: int
    api_budget_limit: int
    api_budget_exhausted: bool


@dataclass(frozen=True, slots=True)
class KanbanGithubBoardItem:
    issue_number: int
    snapshot: dict[str, Any]
    card: dict[str, Any]


@dataclass(frozen=True, slots=True)
class KanbanGithubSyncResult:
    owner: str
    repo: str
    last_sync: str
    cache_ttl_seconds: float
    sync_mode: Literal["cold", "warm"]
    metrics: KanbanGithubMetrics
    freshness: dict[str, SourceFreshness]
    items: tuple[KanbanGithubBoardItem, ...]


@dataclass
class _Budget:
    limit: int
    calls: int = 0
    exhausted: bool = False

    def consume(self) -> bool:
        if self.exhausted:
            return False
        limit = _test_budget_limit.get()
        if limit is not None:
            calls = (_test_budget_calls.get() or 0) + 1
            _test_budget_calls.set(calls)
            if calls > limit:
                raise AssertionError(f"kanban github sync exceeded test budget ({calls} > {limit})")
        self.calls += 1
        if self.calls > self.limit:
            self.exhausted = True
            return False
        return True


@contextmanager
def github_sync_budget_guard(*, max_calls: int) -> Iterator[None]:
    """Test helper: fail if sync records more than max_calls GitHub API calls."""
    token_limit = _test_budget_limit.set(max_calls)
    token_calls = _test_budget_calls.set(0)
    try:
        yield
    finally:
        _test_budget_limit.reset(token_limit)
        _test_budget_calls.reset(token_calls)


def record_github_api_call() -> None:
    """Test hook counted by :func:`github_sync_budget_guard`."""
    limit = _test_budget_limit.get()
    if limit is None:
        return
    calls = (_test_budget_calls.get() or 0) + 1
    _test_budget_calls.set(calls)
    if calls > limit:
        raise AssertionError(f"kanban github sync exceeded test budget ({calls} > {limit})")


def reset_kanban_github_cache() -> None:
    _cache.clear()


def _full_name(owner: str, repo: str) -> str:
    return f"{owner}/{repo}"


def _iso_now(now: Callable[[], datetime]) -> str:
    return now().astimezone(UTC).isoformat().replace("+00:00", "Z")


def _board_cache_key(owner: str, repo: str) -> str:
    return f"kanban:github:board:{owner}/{repo}"


def _parse_linked_issues(body: str | None) -> list[int]:
    if not body:
        return []
    return sorted({int(m) for m in _CLOSING.findall(body)})


def _issue_labels(raw: dict) -> tuple[str, ...]:
    labels = raw.get("labels") or []
    out: list[str] = []
    for lbl in labels:
        if isinstance(lbl, dict) and lbl.get("name"):
            out.append(str(lbl["name"]))
        elif isinstance(lbl, str):
            out.append(lbl)
    return tuple(out)


def _checks_url(full: str, head_sha: str) -> str:
    return f"https://github.com/{full}/commit/{head_sha}"


def _card_to_dict(card: KanbanCard) -> dict[str, Any]:
    return {
        "owner": card.owner,
        "repo": card.repo,
        "issue_number": card.issue_number,
        "column": card.column.value,
        "column_label": card.column_label,
        "matched_columns": [c.value for c in card.matched_columns],
        "stale_reviews": [
            {
                "commit_id": r.commit_id,
                "state": r.state,
                "author": r.author,
                "html_url": r.html_url,
            }
            for r in card.stale_reviews
        ],
        "reasons": list(card.reasons),
        "sources": [{"kind": s.kind, "label": s.label, "url": s.url, "available": s.available} for s in card.sources],
    }


def _snapshot_to_mapping(
    owner: str,
    repo: str,
    issue: KanbanIssueSnapshot,
    pull_request: KanbanPullRequestSnapshot | None,
    auto_merge: KanbanAutoMergeSnapshot | None,
    checks: KanbanChecksSnapshot | None,
    reviews: KanbanReviewsSnapshot | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "issue": {
            "available": issue.available,
            "number": issue.number,
            "state": issue.state,
            "html_url": issue.html_url,
            "labels": list(issue.labels),
            "degraded": issue.degraded,
        },
    }
    if pull_request is not None:
        payload["pull_request"] = {
            "available": pull_request.available,
            "number": pull_request.number,
            "state": pull_request.state,
            "draft": pull_request.draft,
            "head_sha": pull_request.head_sha,
            "html_url": pull_request.html_url,
            "in_merge_queue": pull_request.in_merge_queue,
            "degraded": pull_request.degraded,
        }
    if auto_merge is not None:
        payload["auto_merge"] = {
            "available": auto_merge.available,
            "enabled": auto_merge.enabled,
            "degraded": auto_merge.degraded,
        }
    if checks is not None:
        payload["checks"] = {
            "available": checks.available,
            "head_state": checks.head_state,
            "html_url": checks.html_url,
            "degraded": checks.degraded,
        }
    if reviews is not None:
        payload["reviews"] = {
            "available": reviews.available,
            "head_sha": reviews.head_sha,
            "review_requested": reviews.review_requested,
            "changes_requested_at_head": reviews.changes_requested_at_head,
            "degraded": reviews.degraded,
            "reviews": [
                {
                    "commit_id": r.commit_id,
                    "state": r.state,
                    "author": r.author,
                    "html_url": r.html_url,
                }
                for r in reviews.reviews
            ],
        }
    return payload


async def _cached_get(
    key: str,
    *,
    ttl: float,
    budget: _Budget,
    metrics: dict[str, int],
    fetch: Callable[[], Awaitable[Any]],
) -> tuple[Any, bool]:
    cached = _cache.get(key, ttl)
    if cached is not None:
        metrics["cache_hits"] += 1
        return cached, True
    metrics["cache_misses"] += 1
    if not budget.consume():
        raise KanbanGithubSourceError("budget", "github api budget exhausted")
    record_github_api_call()
    metrics["github_api_calls"] += 1
    data = await fetch()
    _cache.set(key, data, ttl=ttl)
    return data, False


async def _github_list_pages(
    path: str,
    *,
    per_page: int,
    fetch_page: Callable[[str], Awaitable[tuple[list[dict], str | None]]],
    budget: _Budget,
    metrics: dict[str, int],
) -> list[dict]:
    """Follow GitHub ``Link`` pagination with one API call per page (bounded)."""
    items: list[dict] = []
    sep = "&" if "?" in path else "?"
    url: str | None = f"{path}{sep}per_page={per_page}"
    while url:
        if not budget.consume():
            raise KanbanGithubSourceError("budget", "github api budget exhausted")
        record_github_api_call()
        metrics["github_api_calls"] += 1
        page_items, url = await fetch_page(url)
        items.extend(page_items)
    return items


async def _default_list_page_fetch(url: str) -> tuple[list[dict], str | None]:
    """One GitHub list page via gh_client (Link header pagination, #4278 helper path)."""
    from gh_client import _parse_next_link, _request  # noqa: PLC0415

    resp = await _request("GET", url)
    body = resp.json()
    items = body if isinstance(body, list) else []
    return items, _parse_next_link(resp.headers.get("link", ""))


def _map_reviews(
    head_sha: str,
    raw_reviews: list[dict],
    *,
    review_requested: bool,
    degraded: bool = False,
) -> KanbanReviewsSnapshot:
    mapped: list[KanbanHeadReview] = []
    changes_at_head = False
    for row in raw_reviews:
        state = str(row.get("state") or "").upper()
        commit_id = str(row.get("commit_id") or "")
        author = str((row.get("user") or {}).get("login") or "")
        if state == "CHANGES_REQUESTED" and commit_id == head_sha:
            changes_at_head = True
        mapped.append(
            KanbanHeadReview(
                commit_id=commit_id,
                state=state,
                author=author,
                html_url=str(row.get("html_url") or ""),
            )
        )
    return KanbanReviewsSnapshot(
        available=not degraded,
        head_sha=head_sha,
        reviews=tuple(mapped),
        review_requested=review_requested,
        changes_requested_at_head=changes_at_head,
        degraded=degraded,
    )


def _merge_queue_from_timeline(events: list[dict]) -> bool:
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        name = str(event.get("event") or "")
        if name == "added_to_merge_queue":
            return True
        if name == "removed_from_merge_queue":
            return False
    return False


async def _enrich_pull(
    full: str,
    pr_row: dict,
    *,
    get: Callable[[str], Awaitable[Any]],
    ttl: float,
    budget: _Budget,
    metrics: dict[str, int],
) -> tuple[
    KanbanPullRequestSnapshot,
    KanbanAutoMergeSnapshot,
    KanbanChecksSnapshot,
    KanbanReviewsSnapshot,
]:
    number = int(pr_row["number"])
    head_sha = str((pr_row.get("head") or {}).get("sha") or "")
    key = f"kanban:gh:{full}:pull:{number}"
    pr_payload, _ = await _cached_get(
        key,
        ttl=ttl,
        budget=budget,
        metrics=metrics,
        fetch=lambda: get(f"/repos/{full}/pulls/{number}"),
    )
    head_sha = str((pr_payload.get("head") or {}).get("sha") or head_sha)
    state = "merged" if pr_payload.get("merged_at") else str(pr_payload.get("state") or "open")
    pr_snap = KanbanPullRequestSnapshot(
        available=True,
        number=number,
        state=state,
        draft=bool(pr_payload.get("draft")),
        head_sha=head_sha,
        html_url=str(pr_payload.get("html_url") or ""),
        in_merge_queue=False,
    )
    auto_enabled = pr_payload.get("auto_merge") is not None
    in_queue = False
    if auto_enabled:
        in_queue = False
    else:
        try:
            timeline, _ = await _cached_get(
                f"kanban:gh:{full}:mq:{number}",
                ttl=ttl,
                budget=budget,
                metrics=metrics,
                fetch=lambda: get(f"/repos/{full}/issues/{number}/timeline?per_page=30"),
            )
            in_queue = _merge_queue_from_timeline(timeline if isinstance(timeline, list) else [])
        except KanbanGithubSourceError:
            in_queue = False
    pr_snap = KanbanPullRequestSnapshot(
        available=True,
        number=number,
        state=state,
        draft=bool(pr_payload.get("draft")),
        head_sha=head_sha,
        html_url=str(pr_payload.get("html_url") or ""),
        in_merge_queue=in_queue,
    )
    auto = KanbanAutoMergeSnapshot(available=True, enabled=auto_enabled)
    checks_raw, _ = await _cached_get(
        f"kanban:gh:{full}:checks:{head_sha}",
        ttl=ttl,
        budget=budget,
        metrics=metrics,
        fetch=lambda: get(f"/repos/{full}/commits/{head_sha}/check-runs?per_page=100"),
    )
    runs = checks_raw.get("check_runs") if isinstance(checks_raw, dict) else []
    checks = KanbanChecksSnapshot(
        available=True,
        head_state=ci_state(runs or []),
        html_url=_checks_url(full, head_sha),
    )
    reviews_raw, _ = await _cached_get(
        f"kanban:gh:{full}:reviews:{number}",
        ttl=ttl,
        budget=budget,
        metrics=metrics,
        fetch=lambda: get(f"/repos/{full}/pulls/{number}/reviews"),
    )
    review_requested = bool(pr_row.get("requested_reviewers"))
    reviews_list = reviews_raw if isinstance(reviews_raw, list) else []
    reviews = _map_reviews(head_sha, reviews_list, review_requested=review_requested)
    return pr_snap, auto, checks, reviews


async def sync_repo_github_kanban(
    owner: str,
    repo: str,
    *,
    github_get: Callable[[str], Awaitable[Any]] | None = None,
    list_page_fetcher: Callable[[str], Awaitable[tuple[list[dict], str | None]]] | None = None,
    now: Callable[[], datetime] | None = None,
    force_cold: bool = False,
) -> KanbanGithubSyncResult:
    """Sync GitHub-backed kanban snapshots for one repository."""
    import gh_client  # noqa: PLC0415

    full = _full_name(owner, repo)
    now_fn = now or (lambda: datetime.now(tz=UTC))
    synced_at = _iso_now(now_fn)
    ttl = KANBAN_GITHUB_CACHE_TTL_SECONDS

    board_key = _board_cache_key(owner, repo)
    if not force_cold:
        cached_board = _cache.get(board_key, ttl)
        if isinstance(cached_board, dict):
            metrics = KanbanGithubMetrics(
                github_api_calls=0,
                cache_hits=1,
                cache_misses=0,
                api_budget_limit=KANBAN_GITHUB_WARM_BUDGET,
                api_budget_exhausted=False,
            )
            return KanbanGithubSyncResult(
                owner=owner,
                repo=repo,
                last_sync=str(cached_board.get("last_sync") or synced_at),
                cache_ttl_seconds=ttl,
                sync_mode="warm",
                metrics=metrics,
                freshness={k: SourceFreshness(**v) for k, v in cached_board["freshness"].items()},
                items=tuple(KanbanGithubBoardItem(**row) for row in cached_board["items"]),
            )

    get = github_get or gh_client.get

    budget = _Budget(limit=KANBAN_GITHUB_COLD_BUDGET)
    tallies = {"github_api_calls": 0, "cache_hits": 0, "cache_misses": 0}
    freshness: dict[str, SourceFreshness] = {
        "issues": SourceFreshness("ok", synced_at),
        "pull_requests": SourceFreshness("ok", synced_at),
        "checks": SourceFreshness("ok", synced_at),
        "reviews": SourceFreshness("ok", synced_at),
        "auto_merge": SourceFreshness("ok", synced_at),
    }

    fetch_page = list_page_fetcher or _default_list_page_fetch

    try:
        issues = await _github_list_pages(
            f"/repos/{full}/issues?state=open",
            per_page=100,
            fetch_page=fetch_page,
            budget=budget,
            metrics=tallies,
        )
    except KanbanGithubSourceError as exc:
        freshness["issues"] = SourceFreshness("degraded", synced_at, str(exc))
        issues = []

    pull_rows: list[dict] = []
    try:
        pull_rows, _ = await _cached_get(
            f"kanban:gh:{full}:pulls:open",
            ttl=ttl,
            budget=budget,
            metrics=tallies,
            fetch=lambda: get(f"/repos/{full}/pulls?state=open&per_page=100"),
        )
        if not isinstance(pull_rows, list):
            pull_rows = []
    except KanbanGithubSourceError as exc:
        freshness["pull_requests"] = SourceFreshness("degraded", synced_at, str(exc))

    issue_to_pr: dict[int, dict] = {}
    for pr in pull_rows:
        if not isinstance(pr, dict):
            continue
        for issue_num in _parse_linked_issues(str(pr.get("body") or "")):
            issue_to_pr.setdefault(issue_num, pr)

    items: list[KanbanGithubBoardItem] = []
    for raw_issue in issues:
        if not isinstance(raw_issue, dict):
            continue
        if raw_issue.get("pull_request"):
            continue
        number = int(raw_issue["number"])
        issue_snap = KanbanIssueSnapshot(
            available=True,
            number=number,
            state=str(raw_issue.get("state") or "open"),
            html_url=str(raw_issue.get("html_url") or ""),
            labels=_issue_labels(raw_issue),
            degraded=freshness["issues"].status == "degraded",
        )
        pr_row = issue_to_pr.get(number)
        pull: KanbanPullRequestSnapshot | None = None
        auto: KanbanAutoMergeSnapshot | None = None
        checks: KanbanChecksSnapshot | None = None
        reviews: KanbanReviewsSnapshot | None = None

        if pr_row is not None:
            try:
                pull, auto, checks, reviews = await _enrich_pull(
                    full,
                    pr_row,
                    get=get,
                    ttl=ttl,
                    budget=budget,
                    metrics=tallies,
                )
            except KanbanGithubSourceError as exc:
                freshness["pull_requests"] = SourceFreshness("degraded", synced_at, str(exc))
                pull = KanbanPullRequestSnapshot(
                    available=False,
                    number=int(pr_row.get("number") or 0),
                    state="open",
                    draft=False,
                    head_sha="",
                    html_url=str(pr_row.get("html_url") or ""),
                    degraded=True,
                )
                auto = KanbanAutoMergeSnapshot(available=False, degraded=True)
                reviews = KanbanReviewsSnapshot(
                    available=False,
                    head_sha="",
                    review_requested=bool(pr_row.get("requested_reviewers")),
                    degraded=True,
                )

        mapping = _snapshot_to_mapping(owner, repo, issue_snap, pull, auto, checks, reviews)
        card = project_kanban_column(snapshot_from_mapping(mapping))
        items.append(
            KanbanGithubBoardItem(
                issue_number=number,
                snapshot=mapping,
                card=_card_to_dict(card),
            )
        )

    result_metrics = KanbanGithubMetrics(
        github_api_calls=tallies["github_api_calls"],
        cache_hits=tallies["cache_hits"],
        cache_misses=tallies["cache_misses"],
        api_budget_limit=KANBAN_GITHUB_COLD_BUDGET,
        api_budget_exhausted=budget.exhausted,
    )
    board_payload = {
        "last_sync": synced_at,
        "freshness": {
            key: {"status": val.status, "synced_at": val.synced_at, "detail": val.detail}
            for key, val in freshness.items()
        },
        "items": [asdict(item) for item in items],
    }
    _cache.set(board_key, board_payload, ttl=ttl)

    return KanbanGithubSyncResult(
        owner=owner,
        repo=repo,
        last_sync=synced_at,
        cache_ttl_seconds=ttl,
        sync_mode="cold",
        metrics=result_metrics,
        freshness=freshness,
        items=tuple(items),
    )
