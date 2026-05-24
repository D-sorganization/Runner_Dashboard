"""GitHub Actions workflow run and job enrichment helpers.

Pure extraction from server.py of the functions that fetch and annotate
workflow run data with runner placement information.

The ORG constant and run_cmd helper are read/imported at module level to
match original server.py behaviour.  Tests may monkeypatch module attributes.
"""

from __future__ import annotations

import json
import logging
import os

from pydantic import BaseModel, Field
from system_utils import run_cmd  # noqa: E402

log = logging.getLogger("dashboard")

# ---------------------------------------------------------------------------
# Module-level constants (mirrors server.py)
# ---------------------------------------------------------------------------

ORG: str = os.environ.get("GITHUB_ORG", "D-sorganization")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Run(BaseModel):
    """Minimal representation of a GitHub Actions workflow run."""

    id: int
    name: str | None = None
    status: str | None = None
    conclusion: str | None = None
    model_config = {"extra": "allow"}


class Job(BaseModel):
    """Minimal representation of a GitHub Actions workflow job."""

    id: int
    name: str | None = None
    status: str | None = None
    conclusion: str | None = None
    runner_name: str | None = None
    runner_id: int | None = None
    runner_group_name: str | None = None
    labels: list[str] = Field(default_factory=list)
    model_config = {"extra": "allow"}


class JobPlacement(BaseModel):
    """Runner placement extracted from a job."""

    runner_id: int | None = None
    runner_name: str | None = None
    runner_group_name: str | None = None
    runner_labels: list[str] = Field(default_factory=list)
    machine_name: str | None = None


class EnrichedRun(BaseModel):
    """A workflow run enriched with runner placement information."""

    id: int
    name: str | None = None
    status: str | None = None
    conclusion: str | None = None
    runner_id: int | None = None
    runner_name: str | None = None
    runner_group_name: str | None = None
    runner_labels: list[str] = Field(default_factory=list)
    machine_name: str | None = None
    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _machine_name_from_runner_name(runner_name: str | None) -> str | None:
    """Normalise fleet runner names to dashboard machine names."""
    if not runner_name:
        return None
    name = str(runner_name).strip()
    prefix = "d-sorg-local-"
    if not name.startswith(prefix):
        return name
    stem = name.removeprefix(prefix)
    machine, separator, suffix = stem.rpartition("-")
    if separator and suffix.isdigit() and machine:
        return machine
    return stem


def _repo_name_from_run(run: dict) -> str | None:
    """Return the repository name from either normalised or raw run payloads."""
    repo = run.get("repository")
    if isinstance(repo, dict) and repo.get("name"):
        return str(repo["name"])
    if run.get("_repo"):
        return str(run["_repo"])
    return None


# ---------------------------------------------------------------------------
# Public async functions
# ---------------------------------------------------------------------------


async def _get_recent_org_repos(limit: int = 30) -> list[dict]:
    """Fetch recently updated organisation repositories.

    Pre-condition: limit is a positive int.
    Post-condition: returns a list (possibly empty) of repository dicts.
    """
    assert isinstance(limit, int) and limit > 0, f"limit must be a positive int, got {limit!r}"

    code, stdout, _ = await run_cmd(
        [
            "gh",
            "api",
            f"/orgs/{ORG}/repos?per_page={limit}&sort=updated&direction=desc",
        ],
        timeout=20,
    )
    if code != 0:
        return []
    try:
        result = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return []

    assert isinstance(result, list)
    return result


async def _fetch_repo_runs(
    repo_name: str,
    *,
    per_page: int = 10,
    status: str | None = None,
) -> list[dict]:
    """Fetch workflow runs for one repository and annotate repository name.

    Pre-condition: repo_name is a non-empty string.
    Post-condition: every returned run dict has a 'repository' key with a
                    'name' sub-key.
    """
    assert isinstance(repo_name, str) and repo_name, f"repo_name must be a non-empty str, got {repo_name!r}"

    status_part = f"&status={status}" if status else ""
    rc, out, _ = await run_cmd(
        [
            "gh",
            "api",
            f"/repos/{ORG}/{repo_name}/actions/runs?per_page={per_page}{status_part}",
        ],
        timeout=15,
    )
    if rc != 0:
        return []
    try:
        runs = json.loads(out).get("workflow_runs", [])
    except (json.JSONDecodeError, ValueError):
        return []

    for run in runs:
        if "repository" not in run or not run["repository"]:
            run["repository"] = {"name": repo_name}

    assert all("repository" in r for r in runs)
    return runs


async def _fetch_run_jobs(repo_name: str, run_id: int | str) -> list[dict]:
    """Fetch job-level data for one workflow run.

    Pre-condition: repo_name is a non-empty string; run_id is an int or str.
    Post-condition: returns a list (possibly empty) of job dicts.
    """
    assert isinstance(repo_name, str) and repo_name, f"repo_name must be a non-empty str, got {repo_name!r}"
    assert isinstance(run_id, (int, str)) and run_id, f"run_id must be a non-empty int or str, got {run_id!r}"

    rc, out, _ = await run_cmd(
        [
            "gh",
            "api",
            f"/repos/{ORG}/{repo_name}/actions/runs/{run_id}/jobs?per_page=100",
        ],
        timeout=10,
    )
    if rc != 0:
        return []
    try:
        result = json.loads(out).get("jobs", [])
    except (json.JSONDecodeError, ValueError):
        return []

    assert isinstance(result, list)
    return result


async def _fetch_failed_log_excerpt(repo_name: str, run_id: int | str) -> str:
    """Best-effort failed-log excerpt for a workflow run.

    Post-condition: returns a string of at most 12 000 characters.
    """
    assert isinstance(repo_name, str) and repo_name, f"repo_name must be a non-empty str, got {repo_name!r}"

    code, stdout, _ = await run_cmd(
        [
            "gh",
            "run",
            "view",
            str(run_id),
            "--repo",
            f"{ORG}/{repo_name}",
            "--log-failed",
        ],
        timeout=20,
    )
    if code != 0:
        return ""
    text = stdout.strip()
    if not text:
        return ""
    result = text[:12000]
    assert len(result) <= 12000
    return result


# ---------------------------------------------------------------------------
# Public sync functions
# ---------------------------------------------------------------------------


def _placement_from_jobs(jobs: list[dict]) -> dict:
    """Extract machine placement fields from a run's jobs.

    Pre-condition: jobs is a list of dicts.
    Post-condition: returns a dict (empty if no runner_name found).
    """
    assert isinstance(jobs, list), f"jobs must be list, got {type(jobs)!r}"

    for job in jobs:
        runner_name = job.get("runner_name")
        if not runner_name:
            continue
        result = {
            "runner_id": job.get("runner_id"),
            "runner_name": runner_name,
            "runner_group_name": job.get("runner_group_name"),
            "runner_labels": job.get("labels") or [],
            "machine_name": _machine_name_from_runner_name(str(runner_name)),
        }
        assert isinstance(result, dict)
        return result
    return {}


async def _enrich_run_with_job_placement(run: dict) -> dict:
    """Attach job-level runner placement fields to a workflow run.

    Pre-condition: run is a dict.
    Post-condition: returned dict has a 'machine_name' key.
    """
    assert isinstance(run, dict), f"run must be dict, got {type(run)!r}"

    item = dict(run)
    repo_name = _repo_name_from_run(item)
    run_id = item.get("id")
    if repo_name and run_id:
        placement = _placement_from_jobs(await _fetch_run_jobs(repo_name, run_id))
        if placement:
            item.update(placement)
            assert "machine_name" in item
            return item
    machine_name = _machine_name_from_runner_name(item.get("runner_name"))
    item.setdefault("machine_name", machine_name or "GitHub")

    assert "machine_name" in item
    return item
