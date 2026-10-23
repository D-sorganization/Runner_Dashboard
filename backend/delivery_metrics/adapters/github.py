"""GitHub REST collector via :mod:`gh_client` (issue #2118, epic #2117)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from urllib.parse import quote

import gh_client
from cache_utils import Cache
from delivery_metrics.models import CheckRunSnapshot, PrSnapshot
from delivery_metrics.parse import parse_check_runs, parse_pr_snapshot, parse_pull_list_item
from gh_client import GhRateLimited, get_auth_fingerprint

log = logging.getLogger("dashboard.delivery_metrics.github")

_CACHE = Cache(name="delivery_metrics", deepcopy_on_set=True)
_DEFAULT_PULL_TTL_S = 120.0
_DEFAULT_CHECK_TTL_S = 300.0
_PER_PAGE = 100


def _cache_prefix() -> str:
    return f"dm:{get_auth_fingerprint()}"


def _pull_cache_key(repo_full: str, number: int) -> str:
    return f"{_cache_prefix()}:pull:{repo_full}:{number}"


def _check_cache_key(repo_full: str, head_sha: str) -> str:
    return f"{_cache_prefix()}:checks:{repo_full}:{head_sha}"


def _list_cache_key(repo_full: str, since_iso: str, page: int) -> str:
    return f"{_cache_prefix()}:pulls:{repo_full}:{since_iso}:p{page}"


class GhClientDeliveryCollector:
    """Incremental GitHub collector with TTL caching and bounded concurrency."""

    def __init__(
        self,
        *,
        pull_ttl_s: float = _DEFAULT_PULL_TTL_S,
        check_ttl_s: float = _DEFAULT_CHECK_TTL_S,
        concurrency: int = 4,
    ) -> None:
        assert concurrency > 0
        self._pull_ttl = pull_ttl_s
        self._check_ttl = check_ttl_s
        self._sem = asyncio.Semaphore(concurrency)

    async def list_pulls_updated_since(
        self,
        repo_full: str,
        since: datetime,
        *,
        max_pages: int = 3,
    ) -> tuple[PrSnapshot, ...]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        since_iso = since.isoformat().replace("+00:00", "Z")
        collected: list[PrSnapshot] = []
        for page in range(1, max_pages + 1):
            page_key = _list_cache_key(repo_full, since_iso, page)
            cached = _CACHE.get(page_key, self._pull_ttl)
            if cached is not None:
                items = cached
            else:
                path = (
                    f"/repos/{repo_full}/pulls?state=all&sort=updated&direction=desc&per_page={_PER_PAGE}&page={page}"
                )
                async with self._sem:
                    try:
                        body = await gh_client.get(path)
                    except GhRateLimited:
                        log.warning("delivery_metrics: rate limited listing pulls for %s", repo_full)
                        raise
                if not isinstance(body, list):
                    break
                items = body
                _CACHE.set(page_key, items, ttl=self._pull_ttl)
            if not items:
                break
            stop = False
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                pr = parse_pull_list_item(repo_full, raw)
                if pr.updated_at < since:
                    stop = True
                    break
                collected.append(pr)
            if stop or len(items) < _PER_PAGE:
                break
        return tuple(collected)

    async def fetch_pull(self, repo_full: str, number: int) -> PrSnapshot:
        key = _pull_cache_key(repo_full, number)
        cached = _CACHE.get(key, self._pull_ttl)
        if cached is not None:
            return parse_pr_snapshot(repo_full, cached)
        path = f"/repos/{repo_full}/pulls/{number}"
        async with self._sem:
            body = await gh_client.get(path)
        if not isinstance(body, dict):
            raise TypeError(f"expected pull dict from GitHub for {repo_full}#{number}")
        _CACHE.set(key, body, ttl=self._pull_ttl)
        return parse_pr_snapshot(repo_full, body)

    async def list_check_runs(self, repo_full: str, head_sha: str) -> tuple[CheckRunSnapshot, ...]:
        key = _check_cache_key(repo_full, head_sha)
        cached = _CACHE.get(key, self._check_ttl)
        if cached is not None:
            return parse_check_runs(repo_full, head_sha, cached)
        ref = quote(head_sha, safe="")
        path = f"/repos/{repo_full}/commits/{ref}/check-runs?per_page=100"
        async with self._sem:
            body = await gh_client.get(path)
        if not isinstance(body, dict):
            body = {"check_runs": []}
        _CACHE.set(key, body, ttl=self._check_ttl)
        return parse_check_runs(repo_full, head_sha, body)
