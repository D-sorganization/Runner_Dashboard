"""GET /api/repos enrichment cache (#3175)."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from cache_utils import Cache, cache_delete, cache_get, cache_set

log = logging.getLogger("dashboard.repos_enrichment")

ORG_INVENTORY_CACHE_KEY = "repos:org_inventory"
_AGGREGATE_KEY = "repos"
_STALE_TTL = 60.0 * 60.0 * 24.0 * 365.0
_BATCH = 5

_enrichment = Cache(name="repos_enrichment", max_size=250, deepcopy_on_set=True)
assert _enrichment._max_size > 0

RunCmd = Callable[[list[str], int], Awaitable[tuple[int, str, str]]]


def reset_repos_enrichment_cache_for_tests() -> None:
    _enrichment.clear()


def invalidate_repos_enrichment(full_name: str | None = None) -> None:
    if full_name is None:
        _enrichment.clear()
        cache_delete(_AGGREGATE_KEY)
        cache_delete(ORG_INVENTORY_CACHE_KEY)
    else:
        _enrichment.delete(f"repos:enrich:{full_name}")
        cache_delete(_AGGREGATE_KEY)


def _peek(key: str, max_age: float) -> Any | None:
    assert max_age > 0
    entry = _enrichment._store.get(key)
    if entry is None:
        return None
    data, ts = entry
    return data if time.monotonic() - ts < max_age else None


def _gh_subprocess_sync(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    assert cmd and cmd[0] == "gh" and timeout > 0
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)  # noqa: S603
        return (
            p.returncode if p.returncode is not None else -1,
            p.stdout.decode("utf-8", errors="replace"),
            p.stderr.decode("utf-8", errors="replace"),
        )
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


async def _run_gh_off_loop(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    return await asyncio.to_thread(_gh_subprocess_sync, cmd, timeout)


def _base_info(repo: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": repo["name"],
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


def _cached_row(full_name: str, ttl: float) -> tuple[dict[str, Any], str] | None:
    raw = _peek(f"repos:enrich:{full_name}", ttl)
    if not isinstance(raw, dict):
        return None
    info, at = raw.get("info"), raw.get("enriched_at")
    if isinstance(info, dict) and isinstance(at, str):
        return info, at
    return None


async def _fetch_enrichment(repo: dict[str, Any], run_cmd: RunCmd) -> tuple[dict[str, Any], bool]:
    info, ok = _base_info(repo), True
    full = repo["full_name"]
    code, out, _ = await run_cmd(
        ["gh", "api", "--paginate", f"/repos/{full}/pulls?state=open&per_page=100"],
        30,
    )
    if code == 0:
        try:
            info["open_prs"] = len(json.loads(out))
        except (json.JSONDecodeError, ValueError):
            ok = False
    else:
        ok = False
    info["open_issues"] = max(0, info["open_issues_count"] - info["open_prs"])
    code, out, _ = await run_cmd(["gh", "api", f"/repos/{full}/actions/runs?per_page=1"], 15)
    if code == 0:
        try:
            data = json.loads(out)
            runs = data.get("workflow_runs", []) if isinstance(data, dict) else []
            if runs:
                last = runs[0]
                info["last_ci_status"] = last.get("status")
                info["last_ci_conclusion"] = last.get("conclusion")
                info["last_ci_run_url"] = last.get("html_url")
                info["last_ci_updated"] = last.get("updated_at")
        except (json.JSONDecodeError, ValueError):
            ok = False
    else:
        ok = False
    return info, ok


async def _enrich_one(
    repo: dict[str, Any],
    *,
    ttl: float,
    run_cmd: RunCmd,
) -> tuple[dict[str, Any], str | None, bool]:
    full = repo["full_name"]
    hit = _cached_row(full, ttl)
    if hit:
        return hit[0], hit[1], False
    stale = _cached_row(full, _STALE_TTL)
    try:
        info, ok = await _fetch_enrichment(repo, run_cmd)
        if not ok and stale:
            return stale[0], stale[1], True
        at = datetime.now(UTC).isoformat()
        if ok:
            _enrichment.set(f"repos:enrich:{full}", {"info": info, "enriched_at": at})
            return info, at, False
        return info, at, True
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        log.warning("repo enrichment failed for %s: %r", full, exc)
        if stale:
            return stale[0], stale[1], True
        raise


async def _org_repos(org: str, run_cmd: RunCmd, ttl: float) -> list[dict[str, Any]]:
    cached = cache_get(ORG_INVENTORY_CACHE_KEY, ttl)
    if isinstance(cached, list):
        return cached
    repos: list[dict] = []
    for page in (1, 2):
        code, stdout, _ = await run_cmd(
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
    assert 0 < repos_ttl <= _STALE_TTL
    cached = cache_get(_AGGREGATE_KEY, repos_ttl)
    if isinstance(cached, dict) and isinstance(cached.get("repos"), list):
        return cached

    cmd = run_cmd or _run_gh_off_loop
    repos = await _org_repos(org, cmd, repos_ttl)
    stale_agg = cache_get(_AGGREGATE_KEY, _STALE_TTL)
    if not repos and isinstance(stale_agg, dict):
        out = dict(stale_agg)
        out["data_source"] = "stale"
        return out

    results: list[dict[str, Any]] = []
    latest_at: str | None = None
    refresh_failed = False
    for i in range(0, len(repos), _BATCH):
        batch = repos[i : i + _BATCH]
        rows = await asyncio.gather(*[_enrich_one(r, ttl=repos_ttl, run_cmd=cmd) for r in batch])
        for info, at, failed in rows:
            results.append(info)
            if failed:
                refresh_failed = True
            if at and (latest_at is None or at > latest_at):
                latest_at = at

    results.sort(key=lambda r: r.get("last_ci_updated") or "", reverse=True)
    payload: dict[str, Any] = {"repos": results, "total_count": len(results), "org": org}
    if latest_at:
        payload["enriched_at"] = latest_at
    if refresh_failed:
        payload["data_source"] = "stale"
    cache_set(_AGGREGATE_KEY, payload)
    return payload
