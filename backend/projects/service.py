"""Project overview assembly for ``/api/projects`` (issue #1199, epic #1192).

For every repository in ``config/projects.json`` this module fetches
``docs/project/CHARTER.md`` and ``docs/project/STATUS.md`` from the default branch
through the shared GitHub client (``gh_utils.gh_api``), parses them with the
mirrored charter contract, joins the latest ``project-steward`` run from the staff
run store, and caches the result for ``CACHE_TTL_SECONDS``.

Postconditions: ``project_overview`` never raises for a repository problem — a
missing charter yields ``charter_present: false`` and any fetch/parse failure is
reported in ``error``; the route therefore never answers 5xx for one bad repo.
"""

from __future__ import annotations

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
from projects.charter import CharterError, feature_progress, parse_charter, parse_decisions_needed
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
    }


async def build_project(repo: str, fetch: Fetcher | None = None, store: RunStore | None = None) -> dict[str, Any]:
    """Assemble one repository's overview. Never raises for repository-level problems."""
    result = _empty(repo)
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
