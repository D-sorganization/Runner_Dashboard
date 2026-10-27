"""Per-repository enrichment cache for ``GET /api/repos`` (#3175).

Org-wide repo lists and per-repo PR/CI metadata are cached separately with a
bounded LRU store. ``gh`` subprocess work runs in a worker thread so the asyncio
event loop stays responsive. On refresh failure, callers receive the last good
payload plus a freshness timestamp.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from cache_utils import Cache, cache_delete, cache_get, cache_set

log = logging.getLogger("dashboard.repos_enrichment")

ORG_INVENTORY_CACHE_KEY = "repos:org_inventory"
_AGGREGATE_CACHE_KEY = "repos"

# DbC: bounded cache size must be positive.
_MAX_ENRICHMENT_ENTRIES = 250
assert _MAX_ENRICHMENT_ENTRIES > 0

_ENRICHMENT_FRESH_TTL_DEFAULT = 120.0
_ENRICHMENT_STALE_TTL = 60.0 * 60.0 * 24.0 * 365.0
_ENRICH_BATCH_SIZE = 5

_enrichment_cache = Cache(
    name="repos_enrichment",
    max_size=_MAX_ENRICHMENT_ENTRIES,
    deepcopy_on_set=True,
)

RunCmd = Callable[[list[str], int], Awaitable[tuple[int, str, str]]]


def reset_repos_enrichment_cache_for_tests() -> None:
    """Test-only: drop in-memory enrichment entries."""
    _enrichment_cache.clear()


def invalidate_repos_enrichment(full_name: str | None = None) -> None:
    """Explicit cache invalidation for repo enrichment and aggregate snapshots."""
    if full_name is None:
        _enrichment_cache.clear()
        cache_delete(_AGGREGATE_CACHE_KEY)
        cache_delete(ORG_INVENTORY_CACHE_KEY)
        return
    _enrichment_cache.delete(_enrichment_key(full_name))
    cache_delete(_AGGREGATE_CACHE_KEY)


def _enrichment_key(full_name: str) -> str:
    assert full_name, "full_name is required"
    return f"repos:enrich:{full_name}"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _gh_subprocess_sync(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    """Run ``gh`` synchronously (intended for ``asyncio.to_thread``)."""
    assert cmd and cmd[0] == "gh", "only gh subprocesses are supported"
    assert timeout > 0, "timeout must be positive"
    try:
        completed = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return (
            completed.returncode if completed.returncode is not None else -1,
            completed.stdout.decode("utf-8", errors="replace"),
            completed.stderr.decode("utf-8", errors="replace"),
        )
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


async def _run_gh_off_loop(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    return await asyncio.to_thread(_gh_subprocess_sync, cmd, timeout)


def _base_repo_info(repo: dict[str, Any]) -> dict[str, Any]:
    name = repo["name"]
    return {
        "name": name,
        "full_name": repo["full_name"],
        "description": repo.get("description", ""),
        "url": repo.get("html_url", ""),
        "private": repo.get("private", False),
        "language": repo.get("language"),
        "default_branch": repo.get("default_branch", "main"),
        "updated_at": repo.get("updated_at", ""),
        "open_issues_count": repo.get("open_issues_count", 0),
        "open_prs": 0,
        "open_issues": 0,
        "last_ci_status": None,
        "last_ci_conclusion": None,
        "last_ci_run_url": None,
        "last_ci_updated": None,
    }


async def _fetch_repo_enrichment(
    repo: dict[str, Any],
    *,
    run_cmd: RunCmd,
) -> tuple[dict[str, Any], bool]:
    info = _base_repo_info(repo)
    full_name = repo["full_name"]
    ok = True
    pr_code, pr_out, _ = await run_cmd(
        ["gh", "api", "--paginate", f"/repos/{full_name}/pulls?state=open&per_page=100"],
        30,
    )
    if pr_code == 0:
        try:
            info["open_prs"] = len(json.loads(pr_out))
        except (json.JSONDecodeError, ValueError):
            ok = False
    else:
        ok = False
    info["open_issues"] = max(0, info["open_issues_count"] - info["open_prs"])
    run_code, run_out, _ = await run_cmd(
        ["gh", "api", f"/repos/{full_name}/actions/runs?per_page=1"],
        15,
    )
    if run_code == 0:
        try:
            runs_data = json.loads(run_out)
            runs_list = runs_data.get("workflow_runs", [])
            if runs_list:
                last_run = runs_list[0]
                info["last_ci_status"] = last_run.get("status")
                info["last_ci_conclusion"] = last_run.get("conclusion")
                info["last_ci_run_url"] = last_run.get("html_url")
                info["last_ci_updated"] = last_run.get("updated_at")
        except (json.JSONDecodeError, ValueError):
            ok = False
    else:
        ok = False
    return info, ok


def _read_cached_enrichment(full_name: str, ttl: float) -> dict[str, Any] | None:
    entry = _enrichment_cache.get_within(_enrichment_key(full_name), ttl)
    if not isinstance(entry, dict):
        return None
    info = entry.get("info")
    return info if isinstance(info, dict) else None


def _read_stale_enrichment(full_name: str) -> tuple[dict[str, Any] | None, str | None]:
    entry = _enrichment_cache.get_within(_enrichment_key(full_name), _ENRICHMENT_STALE_TTL)
    if not isinstance(entry, dict):
        return None, None
    info = entry.get("info")
    enriched_at = entry.get("enriched_at")
    if not isinstance(info, dict) or not isinstance(enriched_at, str):
        return None, None
    return info, enriched_at


async def _enrich_repo_cached(
    repo: dict[str, Any],
    *,
    fresh_ttl: float,
    run_cmd: RunCmd,
) -> tuple[dict[str, Any], str | None, bool]:
    """Return (info, enriched_at, refresh_failed)."""
    full_name = repo["full_name"]
    fresh = _read_cached_enrichment(full_name, fresh_ttl)
    if fresh is not None:
        entry = _enrichment_cache.get_within(_enrichment_key(full_name), fresh_ttl)
        enriched_at = entry.get("enriched_at") if isinstance(entry, dict) else None
        return fresh, enriched_at if isinstance(enriched_at, str) else None, False

    stale_info, stale_at = _read_stale_enrichment(full_name)
    try:
        info, ok = await _fetch_repo_enrichment(repo, run_cmd=run_cmd)
        if not ok:
            if stale_info is not None:
                return stale_info, stale_at, True
            return info, _utc_now_iso(), True
        enriched_at = _utc_now_iso()
        _enrichment_cache.set(
            _enrichment_key(full_name),
            {"info": info, "enriched_at": enriched_at},
        )
        return info, enriched_at, False
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        log.warning("repo enrichment refresh failed for %s: %r", full_name, exc)
        if stale_info is not None:
            return stale_info, stale_at, True
        raise


async def _fetch_org_repos(org: str, *, run_cmd: RunCmd, inventory_ttl: float) -> list[dict[str, Any]]:
    cached = cache_get(ORG_INVENTORY_CACHE_KEY, inventory_ttl)
    if isinstance(cached, list):
        return cached

    repos: list[dict] = []
    for page in range(1, 3):
        code, stdout, _stderr = await run_cmd(
            ["gh", "api", f"/orgs/{org}/repos?per_page=100&page={page}&sort=updated&direction=desc"],
            30,
        )
        if code != 0:
            break
        try:
            batch = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            break
        if not batch:
            break
        repos.extend(batch)

    cache_set(ORG_INVENTORY_CACHE_KEY, repos)
    return repos


async def build_repos_payload(
    *,
    org: str,
    repos_ttl: float,
    run_cmd: RunCmd | None = None,
) -> dict[str, Any]:
    """Build the ``GET /api/repos`` JSON payload with layered caching."""
    assert repos_ttl > 0, "repos_ttl must be positive"
    fresh_ttl = repos_ttl
    assert fresh_ttl <= _ENRICHMENT_STALE_TTL, "fresh_ttl must not exceed stale_ttl"

    aggregate = cache_get(_AGGREGATE_CACHE_KEY, repos_ttl)
    if isinstance(aggregate, dict) and isinstance(aggregate.get("repos"), list):
        return aggregate

    cmd = run_cmd or _run_gh_off_loop
    stale_aggregate = cache_get(_AGGREGATE_CACHE_KEY, _ENRICHMENT_STALE_TTL)
    refresh_failed = False
    enriched_at: str | None = None
    if isinstance(stale_aggregate, dict):
        enriched_at = stale_aggregate.get("enriched_at")
        if not isinstance(enriched_at, str):
            enriched_at = None

    try:
        repos = await _fetch_org_repos(org, run_cmd=cmd, inventory_ttl=repos_ttl)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        log.warning("org repo inventory failed: %r", exc)
        repos = []
        refresh_failed = True

    if not repos and isinstance(stale_aggregate, dict):
        result = dict(stale_aggregate)
        result["data_source"] = "stale"
        if enriched_at:
            result["enriched_at"] = enriched_at
        return result

    results: list[dict[str, Any]] = []
    latest_enriched_at: str | None = enriched_at

    for i in range(0, len(repos), _ENRICH_BATCH_SIZE):
        batch = repos[i : i + _ENRICH_BATCH_SIZE]
        batch_outcomes = await asyncio.gather(
            *[_enrich_repo_cached(repo, fresh_ttl=fresh_ttl, run_cmd=cmd) for repo in batch],
            return_exceptions=True,
        )
        for repo, outcome in zip(batch, batch_outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if isinstance(outcome, (KeyboardInterrupt, SystemExit)):
                    raise outcome
                log.warning("enrichment failed for %s: %r", repo.get("full_name"), outcome)
                stale_info, stale_at = _read_stale_enrichment(repo["full_name"])
                if stale_info is None:
                    results.append(_base_repo_info(repo))
                    refresh_failed = True
                    continue
                results.append(stale_info)
                refresh_failed = True
                if stale_at and (latest_enriched_at is None or stale_at > latest_enriched_at):
                    latest_enriched_at = stale_at
                continue
            info, row_enriched_at, row_failed = outcome
            results.append(info)
            if row_failed:
                refresh_failed = True
            if row_enriched_at and (latest_enriched_at is None or row_enriched_at > latest_enriched_at):
                latest_enriched_at = row_enriched_at

    results.sort(key=lambda r: (r.get("last_ci_updated") or ""), reverse=True)
    payload: dict[str, Any] = {
        "repos": results,
        "total_count": len(results),
        "org": org,
    }
    if latest_enriched_at:
        payload["enriched_at"] = latest_enriched_at
    if refresh_failed:
        payload["data_source"] = "stale"
    cache_set(_AGGREGATE_CACHE_KEY, payload)
    return payload
