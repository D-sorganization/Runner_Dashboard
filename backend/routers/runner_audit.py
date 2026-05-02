"""Hosted-runner billing audit routes (batch 3 extraction, issue #298).

Extracted from server.py.  Tracks workflow runs that executed on
GitHub-hosted runners instead of self-hosted runners to surface
unintended billing events.

Background loop is started by server.py via ``start_audit_loop()``.
"""

from __future__ import annotations

import asyncio
import datetime as _dt_mod
import logging
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

# Python 3.11+ has datetime.UTC; fall back to timezone.utc for 3.10
UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017

log = logging.getLogger("dashboard.runner_audit")
router = APIRouter(tags=["runner_audit"])

# ─── Constants ────────────────────────────────────────────────────────────────

HOSTED_RUNNER_PATTERNS = re.compile(
    r"^(ubuntu-|windows-|macos-|GitHub Actions \d|Hosted Agent)",
    re.IGNORECASE,
)

_AUDIT_INTERVAL_SECONDS = 900  # 15 minutes
_AUDIT_INITIAL_DELAY_SECONDS = 30

# ─── State (module-level, shared with server.py startup hook) ─────────────────

_runner_audit_cache: dict[str, Any] = {
    "violations": [],
    "last_checked": None,
    "error": None,
}
_runner_audit_lock = asyncio.Lock()

# Set by server.py via set_org() so this module doesn't import ORG directly.
_org: str = ""


def set_org(org: str) -> None:
    """Inject the GitHub org name (called by server.py on startup)."""
    global _org  # noqa: PLW0603
    _org = org


# ─── Fetch helpers ────────────────────────────────────────────────────────────


async def _fetch_hosted_runner_violations() -> list[dict[str, Any]]:
    """Query GitHub API for recent runs on hosted runners across org repos."""
    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    org = _org
    violations: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=30) as client:
        repos_resp = await client.get(
            f"https://api.github.com/orgs/{org}/repos",
            headers=headers,
            params={"per_page": 50, "sort": "pushed"},
        )
        if repos_resp.status_code != 200:
            return []
        repos = [r["name"] for r in repos_resp.json()]

        for repo_name in repos[:20]:  # limit to 20 most recently pushed
            try:
                runs_resp = await client.get(
                    f"https://api.github.com/repos/{org}/{repo_name}/actions/runs",
                    headers=headers,
                    params={"per_page": 10, "status": "completed"},
                )
                if runs_resp.status_code != 200:
                    continue
                for run in runs_resp.json().get("workflow_runs", []):
                    jobs_resp = await client.get(
                        f"https://api.github.com/repos/{org}/{repo_name}/actions/runs/{run['id']}/jobs",
                        headers=headers,
                        params={"per_page": 30},
                    )
                    if jobs_resp.status_code != 200:
                        continue
                    for job in jobs_resp.json().get("jobs", []):
                        runner_name = job.get("runner_name") or ""
                        runner_group = job.get("runner_group_name") or ""
                        if HOSTED_RUNNER_PATTERNS.match(runner_name) or runner_group == "GitHub Actions":
                            violations.append(
                                {
                                    "repo": repo_name,
                                    "workflow": run.get("name", ""),
                                    "run_id": run["id"],
                                    "job_name": job.get("name", ""),
                                    "runner_name": runner_name,
                                    "runner_group": runner_group,
                                    "run_url": run.get("html_url", ""),
                                    "started_at": job.get("started_at", ""),
                                    "conclusion": job.get("conclusion", ""),
                                }
                            )
            except Exception as e:  # noqa: BLE001
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                continue

    return violations


async def _run_runner_audit() -> None:
    async with _runner_audit_lock:
        try:
            violations = await _fetch_hosted_runner_violations()
            _runner_audit_cache["violations"] = violations
            _runner_audit_cache["last_checked"] = _dt_mod.datetime.now(UTC).isoformat().replace("+00:00", "Z")
            _runner_audit_cache["error"] = None
            if violations:
                log.warning(
                    "BILLING ALERT: %d workflow run(s) detected on GitHub-hosted runners",
                    len(violations),
                )
        except Exception as exc:  # noqa: BLE001
            _runner_audit_cache["error"] = str(exc)
            log.error("Runner routing audit failed: %s", exc)


async def _runner_audit_loop() -> None:
    await asyncio.sleep(_AUDIT_INITIAL_DELAY_SECONDS)
    while True:
        await _run_runner_audit()
        await asyncio.sleep(_AUDIT_INTERVAL_SECONDS)


def start_audit_loop() -> None:
    """Schedule the background audit loop.  Called once by server.py on startup."""
    asyncio.create_task(_runner_audit_loop())


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("/api/runner-routing-audit")
async def get_runner_routing_audit() -> JSONResponse:
    """Return recent workflow runs that executed on GitHub-hosted runners."""
    return JSONResponse(_runner_audit_cache)


@router.post("/api/runner-routing-audit/refresh")
async def refresh_runner_routing_audit() -> JSONResponse:
    """Trigger an immediate audit refresh."""
    asyncio.create_task(_run_runner_audit())
    return JSONResponse({"status": "refresh triggered"})
