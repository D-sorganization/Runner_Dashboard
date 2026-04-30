"""Issue inventory — fetch and normalise open issues across repositories.

This module is a thin data layer used by the ``/api/issues`` FastAPI route in
``server.py``.  All GitHub interaction goes through the ``gh`` CLI.

Taxonomy labels follow the schema in ``docs/issue-taxonomy.md``:
  ``type:*``, ``complexity:*``, ``effort:*``, ``judgement:*``,
  ``quick-win``, ``panel-review``, ``wave:*``, ``domain:*``.
"""

from __future__ import annotations

import asyncio
import datetime as _dt_mod
import json
import logging
import re
from typing import Any

from cache_utils import Cache

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard")

# ─── In-process cache ────────────────────────────────────────────────────────
_issue_cache = Cache(name="issue_inventory", deepcopy_on_set=True)
_CACHE_TTL = 30.0  # seconds

# ─── Taxonomy constants ───────────────────────────────────────────────────────
_BLOCKED_JUDGEMENTS = {"design", "contested"}

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


def _parse_agent_claim(labels: list[str]) -> tuple[str | None, str | None]:
    """Return ``(agent, claim_label)`` for a ``claim:*`` label, else ``(None, None)``."""
    for lbl in labels:
        if lbl.startswith("claim:"):
            return lbl[len("claim:") :], lbl
    return None, None


def _parse_claim_expiry(body: str | None) -> str | None:
    """Extract expiry timestamp from lease comment pattern in body."""
    if not body:
        return None
    m = re.search(r"lease:\s+\S+\s+expires\s+(\S+)", body, re.IGNORECASE)
    return m.group(1) if m else None


# ─── Taxonomy parsing ─────────────────────────────────────────────────────────


def parse_taxonomy(labels: list[str], body: str = "") -> dict:  # noqa: ARG001
    """Parse issue taxonomy from label list.

    Labels take precedence over any metadata in the body.  Valid prefixes:
    ``type:``, ``complexity:``, ``effort:``, ``judgement:``, ``wave:``,
    ``domain:``.  Boolean flags: ``quick-win``, ``panel-review``.

    Parameters
    ----------
    labels:
        List of label name strings on the issue.
    body:
        Issue body text (currently unused; reserved for future metadata
        fallback).

    Returns
    -------
    dict with keys: type, complexity, effort, judgement, quick_win,
    panel_review, domains, wave.
    """
    taxonomy: dict[str, Any] = {
        "type": None,
        "complexity": None,
        "effort": None,
        "judgement": None,
        "quick_win": False,
        "panel_review": False,
        "domains": [],
        "wave": None,
    }
    domains: list[str] = []
    for lbl in labels:
        if lbl.startswith("type:"):
            taxonomy["type"] = lbl[len("type:") :]
        elif lbl.startswith("complexity:"):
            taxonomy["complexity"] = lbl[len("complexity:") :]
        elif lbl.startswith("effort:"):
            taxonomy["effort"] = lbl[len("effort:") :]
        elif lbl.startswith("judgement:"):
            taxonomy["judgement"] = lbl[len("judgement:") :]
        elif lbl.startswith("wave:"):
            val = lbl[len("wave:") :]
            try:
                taxonomy["wave"] = int(val)
            except ValueError:
                taxonomy["wave"] = val
        elif lbl.startswith("domain:"):
            domains.append(lbl[len("domain:") :])
        elif lbl == "quick-win":
            taxonomy["quick_win"] = True
        elif lbl == "panel-review":
            taxonomy["panel_review"] = True
    taxonomy["domains"] = domains
    return taxonomy


# ─── Pickability ──────────────────────────────────────────────────────────────


def is_pickable(item: dict, has_open_pr: bool) -> tuple[bool, list[str]]:
    """Determine whether an issue is available for agent pickup.

    Rules (all must hold):

    1. ``state == "open"`` (checked on the item itself).
    2. No linked open PR (``has_open_pr`` must be *False*).
    3. No active ``claim:*`` label.
    4. ``judgement`` not in ``{"design", "contested"}``.

    Returns
    -------
    ``(pickable, blocked_by)`` where *blocked_by* is a list of human-readable
    reason strings when *pickable* is *False*.
    """
    blocked: list[str] = []

    if item.get("state", "open") != "open":
        blocked.append("state != open")

    if has_open_pr:
        blocked.append("has linked open PR")

    if item.get("agent_claim") is not None:
        blocked.append(f"active claim: {item['agent_claim']}")

    judgement = (item.get("taxonomy") or {}).get("judgement")
    if judgement in _BLOCKED_JUDGEMENTS:
        blocked.append(f"judgement:{judgement} requires panel review")

    return len(blocked) == 0, blocked


# ─── Normalisation ────────────────────────────────────────────────────────────


def _normalise_issue(issue: dict, full_name: str) -> dict:
    """Convert a raw GitHub issue payload into the canonical inventory shape."""
    labels: list[str] = [lbl["name"] for lbl in (issue.get("labels") or [])]
    assignees: list[str] = [
        a["login"] for a in (issue.get("assignees") or []) if isinstance(a, dict) and a.get("login")
    ]
    agent_claim, _ = _parse_agent_claim(labels)
    claim_expires_at = _parse_claim_expiry(issue.get("body")) if agent_claim else None
    taxonomy = parse_taxonomy(labels, issue.get("body") or "")
    created_at: str = issue.get("created_at") or ""

    return {
        "repository": full_name,
        "number": issue.get("number"),
        "title": issue.get("title") or "",
        "url": issue.get("html_url") or "",
        "author": (issue.get("user") or {}).get("login") or "",
        "assignees": assignees,
        "labels": labels,
        "state": issue.get("state") or "open",
        "age_hours": _age_hours(created_at),
        "taxonomy": taxonomy,
        "agent_claim": agent_claim,
        "claim_expires_at": claim_expires_at,
        "linked_pr": None,  # populated by caller if PR data is available
        "pickable": False,  # computed below
        "pickable_blocked_by": [],
    }


# ─── Public API ───────────────────────────────────────────────────────────────


async def fetch_repo_issues(full_name: str, state: str = "open") -> tuple[list[dict], str | None]:
    """Fetch issues for *full_name* (``owner/repo``).

    GitHub's issues API returns PRs too — we filter them out via the
    ``pull_request`` key that is present on PR objects.

    Returns ``(items, error_message)`` where *error_message* is *None* on
    success.
    """
    gh_state = "open" if state == "open" else "all"
    code, stdout, stderr = await _run_gh(
        ["api", f"/repos/{full_name}/issues?state={gh_state}&per_page=100"],
        timeout=30,
    )
    if code != 0:
        msg = f"{full_name}: gh exit {code}: {stderr.strip()[:200]}"
        log.warning("issue_inventory: %s", msg)
        return [], msg
    try:
        raw = json.loads(stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        msg = f"{full_name}: JSON decode error: {exc}"
        log.warning("issue_inventory: %s", msg)
        return [], msg
    if not isinstance(raw, list):
        msg = f"{full_name}: unexpected response shape"
        log.warning("issue_inventory: %s", msg)
        return [], msg

    items = []
    for issue in raw:
        # Skip pull-requests which appear in the issues endpoint
        if issue.get("pull_request") is not None:
            continue
        items.append(_normalise_issue(issue, full_name))
    return items, None


async def fetch_all_issues(
    repos: list[str],
    *,
    state: str = "open",
    labels: list[str] | None = None,
    assignee: str | None = None,
    pickable_only: bool = False,
    complexity: list[str] | None = None,
    effort: list[str] | None = None,
    judgement: list[str] | None = None,
    limit: int = 500,
) -> dict:
    """Aggregate issues across *repos* with optional filters.

    Parameters
    ----------
    repos:
        List of ``owner/repo`` slugs.
    state:
        ``"open"`` (default) or ``"all"``.
    labels:
        Match any of these label names.
    assignee:
        Filter by assignee login.
    pickable_only:
        When *True*, only pickable issues are returned.
    complexity / effort / judgement:
        Filter by taxonomy dimension (match any provided value).
    limit:
        Maximum number of items (hard cap 2000).
    """
    limit = min(limit, 2000)
    label_set: set[str] = set(labels) if labels else set()
    complexity_set: set[str] = set(complexity) if complexity else set()
    effort_set: set[str] = set(effort) if effort else set()
    judgement_set: set[str] = set(judgement) if judgement else set()

    cache_key = (
        f"issues|{','.join(sorted(repos))}|{state}|{','.join(sorted(label_set))}"
        f"|{assignee}|{pickable_only}|{','.join(sorted(complexity_set))}"
        f"|{','.join(sorted(effort_set))}|{','.join(sorted(judgement_set))}|{limit}"
    )
    cached = _issue_cache.get(cache_key, _CACHE_TTL)
    if cached is not None:
        return cached

    tasks = [fetch_repo_issues(repo, state) for repo in repos]
    results = await asyncio.gather(*tasks)

    items: list[dict] = []
    errors: list[str] = []

    for repo_items, err in results:
        if err:
            errors.append(err)
            continue
        for item in repo_items:
            # Label filter
            if label_set and not label_set.intersection(item["labels"]):
                continue
            # Assignee filter
            if assignee and assignee not in item["assignees"]:
                continue
            # Taxonomy dimension filters
            taxonomy = item.get("taxonomy") or {}
            if complexity_set and taxonomy.get("complexity") not in complexity_set:
                continue
            if effort_set and taxonomy.get("effort") not in effort_set:
                continue
            if judgement_set and taxonomy.get("judgement") not in judgement_set:
                continue

            # Compute pickability (no open PR lookup here — caller can enrich)
            pickable, blocked_by = is_pickable(item, has_open_pr=item["linked_pr"] is not None)
            item["pickable"] = pickable
            item["pickable_blocked_by"] = blocked_by

            if pickable_only and not pickable:
                continue
            items.append(item)

    items.sort(key=lambda x: x["age_hours"])
    items = items[:limit]

    result = {"items": items, "errors": errors}
    _issue_cache.set(cache_key, result)
    return result
