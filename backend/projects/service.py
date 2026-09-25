"""Project overview assembly for ``/api/projects`` (issue #1199, epic #1192).

For every repository in ``config/projects.json`` this module fetches
``docs/project/CHARTER.md`` and ``docs/project/STATUS.md`` from the default branch
through the shared GitHub client (``gh_utils.gh_api``), parses them with the
mirrored charter contract, joins the latest ``project-steward`` run from the staff
run store, and caches the result for ``CACHE_TTL_SECONDS``.

It also joins each repository's open issues/PRs against the charter
(``coverage``), and ``fleet_overview`` layers the owner's priority tiers from
Repository_Management ``config/project_priorities.yaml`` on top (``rollup``).

Postconditions: ``project_overview`` never raises for a repository problem — a
missing charter yields ``charter_present: false`` and any fetch/parse failure is
reported in ``error`` (``coverage_error`` for the open-item join); the route
therefore never answers 5xx for one bad repo. A missing or malformed priority
file leaves every project ``unranked`` and is reported in ``priorities_error``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from cache_utils import cache_get, cache_set
from dashboard_config import ORG
from fastapi import HTTPException
from gh_utils import gh_api
from projects import rollup
from projects.charter import CharterError, Feature, feature_progress, parse_charter, parse_decisions_needed
from projects.coverage import classify
from projects.priorities import PRIORITIES_PATH, PRIORITIES_REPO, PriorityError, ProjectPriority, parse_priorities
from staff.store import RunStore, get_store

log = logging.getLogger("dashboard.projects")

CACHE_TTL_SECONDS = 600.0
CHARTER_PATH = "docs/project/CHARTER.md"
STATUS_PATH = "docs/project/STATUS.md"
STEWARD_ROLE = "project-steward"
DEFAULT_REPOS: tuple[str, ...] = (
    "UpstreamDrift",
    "AffineDrift",
    "Gasification_Model",
    "Tools",
    "Tools_Private",
    "Repository_Management",
    "Runner_Dashboard",
)
_REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_STEWARD_LOOKBACK = 200
_MAX_PAGES = 5  # 500 open items per repo is plenty for a coverage signal

Fetcher = Callable[[str], Awaitable[dict[str, Any]]]


def _default_fetch() -> Fetcher:
    """Resolved at call time so tests can monkeypatch ``service.gh_api``."""
    return gh_api


_CONFIG_PATH = Path(
    os.environ.get(
        "RUNNER_DASHBOARD_PROJECTS_CONFIG",
        str(Path(__file__).resolve().parents[2] / "config" / "projects.json"),
    )
).expanduser()


def configured_repos() -> list[str]:
    """Bare repository names from ``config/projects.json`` (fail-soft to the fleet default)."""
    try:
        payload = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list(DEFAULT_REPOS)
    repos = payload.get("repos") if isinstance(payload, dict) else None
    if not isinstance(repos, list):
        return list(DEFAULT_REPOS)
    valid = [r for r in repos if isinstance(r, str) and _REPO_NAME.match(r)]
    return valid or list(DEFAULT_REPOS)


async def fetch_repo_file(repo: str, path: str, fetch: Fetcher | None = None) -> str | None:
    """Return a file's text from ``repo``'s default branch, or ``None`` when it is absent.

    Omitting ``?ref=`` on the contents API already selects the default branch.
    Other GitHub failures propagate as ``HTTPException`` for the caller to record.
    """
    fetch = fetch or _default_fetch()
    try:
        payload = await fetch(f"/repos/{ORG}/{repo}/contents/{path}")
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, str):
        return None
    try:
        return base64.b64decode(content, validate=False).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"GitHub returned undecodable content for {path}") from exc


async def _paged(endpoint: str, fetch: Fetcher) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(1, _MAX_PAGES + 1):
        batch: Any = await fetch(f"{endpoint}&per_page=100&page={page}")
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(r for r in batch if isinstance(r, dict))
        if len(batch) < 100:
            break
    return rows


async def fetch_open_items(repo: str, fetch: Fetcher | None = None) -> list[dict[str, Any]]:
    """Open issues and pull requests (the issues API returns both)."""
    return await _paged(f"/repos/{ORG}/{repo}/issues?state=open", fetch or _default_fetch())


async def fetch_org_repos(fetch: Fetcher | None = None) -> list[dict[str, Any]]:
    """Every repository in ``GITHUB_ORG`` (cached), for the unregistered-repo check."""
    cached = cache_get("projects:org-repos", CACHE_TTL_SECONDS)
    if cached is None:
        cached = await _paged(f"/orgs/{ORG}/repos?type=all", fetch or _default_fetch())
        cache_set("projects:org-repos", cached)
    return cached


async def load_priorities(fetch: Fetcher | None = None) -> tuple[dict[str, ProjectPriority], str | None]:
    """``(priorities, error)``; never raises. The parsed result is cached like the overviews."""
    cached = cache_get("projects:priorities", CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    result: tuple[dict[str, ProjectPriority], str | None]
    try:
        text = await fetch_repo_file(PRIORITIES_REPO, PRIORITIES_PATH, fetch)
        result = ({}, "priority file not found") if text is None else (parse_priorities(text), None)
    except PriorityError as exc:
        result = ({}, f"priorities invalid: {exc}")
    except HTTPException as exc:
        result = ({}, f"github: {exc.detail}")
    cache_set("projects:priorities", result)
    return result


def last_steward_run(repo: str, store: RunStore | None = None) -> dict[str, Any] | None:
    """Latest ``project-steward`` run recorded for ``repo`` on this node, or ``None``."""
    runs = (store or get_store()).list_runs(limit=_STEWARD_LOOKBACK, role=STEWARD_ROLE)
    for run in runs:  # list_runs is newest-first
        if run.repo == repo:
            return run.to_dict()
    return None


def _empty(repo: str) -> dict[str, Any]:
    return {
        "repo": repo,
        "charter_present": False,
        "features": [],
        "progress": feature_progress([]),
        "status_present": False,
        "decisions_needed": [],
        "last_steward_run": None,
        "coverage": None,
    }


async def build_project(repo: str, fetch: Fetcher | None = None, store: RunStore | None = None) -> dict[str, Any]:
    """Assemble one repository's overview. Never raises for repository-level problems."""
    result = _empty(repo)
    features: list[Feature] = []
    try:
        charter = await fetch_repo_file(repo, CHARTER_PATH, fetch)
        if charter is not None:
            result["charter_present"] = True
            features = parse_charter(charter)
            result["features"] = [f.to_dict() for f in features]
            result["progress"] = feature_progress(features)
        status = await fetch_repo_file(repo, STATUS_PATH, fetch)
        if status is not None:
            result["status_present"] = True
            result["decisions_needed"] = parse_decisions_needed(status)
    except CharterError as exc:
        result["error"] = f"charter invalid: {exc}"
    except HTTPException as exc:
        result["error"] = f"github: {exc.detail}"
    except Exception as exc:  # noqa: BLE001 — one bad repo must not fail the page
        log.warning("projects: %s overview failed: %s", repo, exc)
        result["error"] = f"{type(exc).__name__}: {exc}"
    try:
        result["coverage"] = classify(repo, features, await fetch_open_items(repo, fetch))
    except HTTPException as exc:
        result["coverage_error"] = f"github: {exc.detail}"
    try:
        result["last_steward_run"] = last_steward_run(repo, store)
    except Exception as exc:  # noqa: BLE001 — store trouble is reported, not fatal
        log.warning("projects: %s steward lookup failed: %s", repo, exc)
        result.setdefault("error", f"steward lookup failed: {exc}")
    return result


async def project_overview(repo: str, fetch: Fetcher | None = None, store: RunStore | None = None) -> dict[str, Any]:
    """Cached ``build_project``; the steward run is re-read on every call (it is local and cheap)."""
    key = f"projects:{repo}"
    cached = cache_get(key, CACHE_TTL_SECONDS)
    if cached is None:
        cached = await build_project(repo, fetch, store)
        cache_set(key, cached)
    else:
        cached = {**cached, "last_steward_run": last_steward_run(repo, store)}
    return cached


async def fleet_overview(repos: list[str], fetch: Fetcher | None = None) -> dict[str, Any]:
    """Every repo's overview with priorities attached, P0 first, plus the fleet summary."""
    overviews = await asyncio.gather(*(project_overview(repo, fetch) for repo in repos))
    prio, prio_error = await load_priorities(fetch)
    projects = rollup.sort_by_priority(rollup.attach_priorities(overviews, prio))
    body: dict[str, Any] = {"projects": projects, "summary": rollup.fleet_summary(projects)}
    if prio_error:
        body["priorities_error"] = prio_error
    return body
