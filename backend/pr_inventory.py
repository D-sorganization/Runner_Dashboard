"""PR inventory — fetch and normalise open pull-requests across repositories.

This module is a thin data layer used by the ``/api/prs`` and
``/api/prs/{owner}/{repo}/{number}`` FastAPI routes in ``server.py``.
All GitHub interaction goes through the ``gh`` CLI (same pattern as the rest of
the backend) so no extra credentials are required.
"""

from __future__ import annotations

import asyncio
import datetime as _dt_mod
import json
import logging
import re
import time
from typing import Any

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard")

# ─── In-process cache ────────────────────────────────────────────────────────
_cache: dict[str, tuple[Any, float]] = {}
_CACHE_TTL = 30.0  # seconds


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry is not None:
        data, ts = entry
        if time.monotonic() - ts < _CACHE_TTL:
            return data
    return None


def _cache_set(key: str, data: Any) -> None:
    _cache[key] = (data, time.monotonic())


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _run_gh(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run ``gh`` with the given arguments asynchronously."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, "", "gh CLI not found"
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode or 0,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )
    except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041
        proc.kill()
        return -1, "", "gh command timed out"


def _age_hours(created_at: str) -> float:
    """Return hours since *created_at* (ISO-8601 UTC string), rounded to 1dp."""
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        delta = datetime.now(tz=UTC) - created
        return round(delta.total_seconds() / 3600, 1)
    except (ValueError, AttributeError):
        return 0.0


def _parse_agent_claim(labels: list[str]) -> str | None:
    """Return the agent name from a ``claim:*`` label, or *None*."""
    for lbl in labels:
        if lbl.startswith("claim:"):
            return lbl[len("claim:") :]
    return None


def _parse_linked_issues(body: str | None) -> list[int]:
    """Extract issue numbers referenced by closing keywords in *body*."""
    if not body:
        return []
    matches = re.findall(r"(?:closes|fixes|resolves)\s+#(\d+)", body, re.IGNORECASE)
    return sorted({int(m) for m in matches})


def _normalise_pr(pr: dict, full_name: str) -> dict:
    """Convert a raw GitHub PR payload into the canonical inventory shape."""
    labels: list[str] = [lbl["name"] for lbl in (pr.get("labels") or [])]
    reviewers: list[str] = [
        r["login"]
        for r in (pr.get("requested_reviewers") or [])
        if isinstance(r, dict) and r.get("login")
    ]
    created_at: str = pr.get("created_at") or ""
    return {
        "repository": full_name,
        "number": pr.get("number"),
        "title": pr.get("title") or "",
        "url": pr.get("html_url") or "",
        "author": (pr.get("user") or {}).get("login") or "",
        "draft": bool(pr.get("draft")),
        "age_hours": _age_hours(created_at),
        "labels": labels,
        "requested_reviewers": reviewers,
        "head_ref": (pr.get("head") or {}).get("ref") or "",
        "mergeable_state": pr.get("mergeable_state") or None,
        "agent_claim": _parse_agent_claim(labels),
        "linked_issues": _parse_linked_issues(pr.get("body")),
    }


# ─── Public API ───────────────────────────────────────────────────────────────


async def fetch_repo_prs(full_name: str) -> tuple[list[dict], str | None]:
    """Fetch open PRs for *full_name* (``owner/repo``).

    Returns ``(items, error_message)`` where *error_message* is *None* on
    success.  Per-repo errors are surfaced in the ``errors`` array of the
    aggregated response so a single unavailable repo does not fail the whole
    request.
    """
    code, stdout, stderr = await _run_gh(
        ["api", f"/repos/{full_name}/pulls?state=open&per_page=100"],
        timeout=30,
    )
    if code != 0:
        msg = f"{full_name}: gh exit {code}: {stderr.strip()[:200]}"
        log.warning("pr_inventory: %s", msg)
        return [], msg
    try:
        raw = json.loads(stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        msg = f"{full_name}: JSON decode error: {exc}"
        log.warning("pr_inventory: %s", msg)
        return [], msg
    if not isinstance(raw, list):
        msg = f"{full_name}: unexpected response shape"
        log.warning("pr_inventory: %s", msg)
        return [], msg
    items = [_normalise_pr(pr, full_name) for pr in raw]
    return items, None


async def fetch_all_prs(
    repos: list[str],
    *,
    include_drafts: bool = True,
    author: str | None = None,
    labels: list[str] | None = None,
    limit: int = 500,
) -> dict:
    """Aggregate open PRs across *repos*.

    Parameters
    ----------
    repos:
        List of ``owner/repo`` slugs.
    include_drafts:
        When *False*, draft PRs are excluded.
    author:
        If set, only PRs by this login are returned.
    labels:
        If set, only PRs that have at least one of these labels are returned.
    limit:
        Maximum number of items to return (hard cap 2000).
    """
    limit = min(limit, 2000)
    label_set: set[str] = set(labels) if labels else set()

    cache_key = f"prs|{','.join(sorted(repos))}|{include_drafts}|{author}|{','.join(sorted(label_set))}|{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    tasks = [fetch_repo_prs(repo) for repo in repos]
    results = await asyncio.gather(*tasks)

    items: list[dict] = []
    errors: list[str] = []

    for repo_items, err in results:
        if err:
            errors.append(err)
            continue
        for item in repo_items:
            if not include_drafts and item["draft"]:
                continue
            if author and item["author"] != author:
                continue
            if label_set and not label_set.intersection(item["labels"]):
                continue
            items.append(item)

    # Sort by age descending (newest first)
    items.sort(key=lambda x: x["age_hours"])
    items = items[:limit]

    result = {"items": items, "total": len(items), "errors": errors}
    _cache_set(cache_key, result)
    return result


async def fetch_pr_detail(owner: str, repo: str, number: int) -> dict:
    """Fetch single PR with extra fields: body_excerpt, checks, files stats."""
    full_name = f"{owner}/{repo}"
    cache_key = f"pr_detail|{full_name}|{number}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # Base PR data
    code, stdout, stderr = await _run_gh(
        ["api", f"/repos/{full_name}/pulls/{number}"],
        timeout=20,
    )
    if code != 0:
        raise ValueError(
            f"GitHub API error for {full_name}#{number}: {stderr.strip()[:200]}"
        )
    try:
        pr = json.loads(stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"JSON decode error for {full_name}#{number}: {exc}") from exc

    base = _normalise_pr(pr, full_name)
    body: str = pr.get("body") or ""
    base["body_excerpt"] = body[:2048]
    base["files_changed"] = pr.get("changed_files") or 0
    base["additions"] = pr.get("additions") or 0
    base["deletions"] = pr.get("deletions") or 0

    # Check runs
    head_sha: str = (pr.get("head") or {}).get("sha") or ""
    checks: list[dict] = []
    if head_sha:
        chk_code, chk_out, _ = await _run_gh(
            ["api", f"/repos/{full_name}/commits/{head_sha}/check-runs?per_page=100"],
            timeout=20,
        )
        if chk_code == 0:
            try:
                chk_data = json.loads(chk_out)
                for run in chk_data.get("check_runs") or []:
                    checks.append(
                        {
                            "name": run.get("name") or "",
                            "conclusion": run.get("conclusion"),
                            "url": run.get("html_url") or "",
                        }
                    )
            except (json.JSONDecodeError, ValueError):
                pass

    base["checks"] = checks
    _cache_set(cache_key, base)
    return base
