"""Heavy test dispatch routes.

Extracted from server.py (issue #358).
Routes: GET /api/heavy-tests/repos, POST /api/heavy-tests/dispatch,
        POST /api/heavy-tests/docker.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dashboard_config import ORG
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_scope  # noqa: B008

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("dashboard.heavy_tests")
router = APIRouter(tags=["heavy-tests"])

# ---------------------------------------------------------------------------
# Injected dependencies (set by server.py after import)
# ---------------------------------------------------------------------------

_run_cmd: Callable | None = None
_heavy_test_repos: dict[str, Any] | None = None


def set_dependencies(
    run_cmd: Callable,
    heavy_test_repos: dict[str, Any],
) -> None:
    """Wire server.py helpers into this router (called at startup)."""
    global _run_cmd, _heavy_test_repos  # noqa: PLW0603
    _run_cmd = run_cmd
    _heavy_test_repos = heavy_test_repos


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/heavy-tests/repos")
async def get_heavy_test_repos() -> dict:
    """List repos that support heavy test workflow dispatch."""
    heavy_test_repos: dict = _heavy_test_repos  # type: ignore[assignment]
    repos = []
    for repo_name, config in heavy_test_repos.items():
        recent_runs = []
        code, stdout, _ = await _run_cmd(  # type: ignore[misc]
            [
                "gh",
                "api",
                f"/repos/{ORG}/{repo_name}/actions/workflows/{config['workflow_file']}/runs?per_page=10",
            ],
            timeout=15,
        )
        if code == 0:
            try:
                data = json.loads(stdout)
                for run in data.get("workflow_runs", []):
                    recent_runs.append(
                        {
                            "id": run["id"],
                            "status": run["status"],
                            "conclusion": run.get("conclusion"),
                            "created_at": run.get("created_at"),
                            "updated_at": run.get("updated_at"),
                            "html_url": run.get("html_url"),
                            "head_branch": run.get("head_branch"),
                            "run_number": run.get("run_number"),
                            "triggering_actor": run.get("triggering_actor", {}).get("login"),
                        }
                    )
            except (json.JSONDecodeError, ValueError):
                pass

        repos.append(
            {
                "name": repo_name,
                "workflow_file": config["workflow_file"],
                "description": config["description"],
                "python_versions": config["python_versions"],
                "default_python": config["default_python"],
                "docker_compose": config.get("docker_compose"),
                "recent_runs": recent_runs,
            }
        )
    return {"repos": repos}


@router.post("/api/heavy-tests/dispatch")
async def dispatch_heavy_test(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("heavy-tests.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch a heavy test workflow via GitHub API."""
    heavy_test_repos: dict = _heavy_test_repos  # type: ignore[assignment]
    body = await request.json()
    repo_name = body.get("repo")
    python_version = body.get("python_version", "3.11")
    ref = body.get("ref", "main")

    if repo_name not in heavy_test_repos:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown heavy test repo: {repo_name}",
        )

    config = heavy_test_repos[repo_name]
    workflow_file = config["workflow_file"]

    log.info(
        "Dispatching heavy test: %s/%s (Python %s, ref=%s)",
        repo_name,
        workflow_file,
        python_version,
        ref,
    )

    code, stdout, stderr = await _run_cmd(  # type: ignore[misc]
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"/repos/{ORG}/{repo_name}/actions/workflows/{workflow_file}/dispatches",
            "-f",
            f"ref={ref}",
            "-f",
            f"inputs[python_version]={python_version}",
        ],
        timeout=15,
    )

    if code != 0:
        log.warning(
            "heavy_test dispatch failed: repo=%s workflow=%s stderr=%s",
            repo_name,
            workflow_file,
            stderr[:200],
        )
        raise HTTPException(
            status_code=502,
            detail="Failed to dispatch workflow",
        )

    return {
        "status": "dispatched",
        "repo": repo_name,
        "workflow": workflow_file,
        "python_version": python_version,
        "ref": ref,
        "message": f"Heavy test workflow dispatched for {repo_name}. Check the Actions tab for progress.",
    }


@router.post("/api/heavy-tests/docker")
async def run_docker_heavy_test(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("heavy-tests.dispatch")),  # noqa: B008
) -> dict:
    """Run heavy tests locally in Docker via docker-compose."""
    heavy_test_repos: dict = _heavy_test_repos  # type: ignore[assignment]
    body = await request.json()
    repo_name = body.get("repo")
    python_version = body.get("python_version", "3.11")

    if repo_name not in heavy_test_repos:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown heavy test repo: {repo_name}",
        )

    config = heavy_test_repos[repo_name]
    _default_repos_base = str(Path("/mnt/c") / "Users" / os.environ.get("USER", "diete") / "Repositories")
    _repos_base = Path(os.environ.get("HEAVY_TEST_REPOS_BASE", _default_repos_base))
    repo_path = _repos_base / repo_name

    if not repo_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Repo not found at {repo_path}",
        )

    docker_compose_file = str(config.get("docker_compose", "docker-compose.yml"))
    compose_path = repo_path / docker_compose_file
    if not compose_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"docker-compose file not found: {compose_path}",
        )

    log.info(
        "Starting Docker heavy test for %s (Python %s)",
        repo_name,
        python_version,
    )

    code, stdout, stderr = await _run_cmd(  # type: ignore[misc]
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "run",
            "--rm",
            "-e",
            f"PYTHON_VERSION={python_version}",
            "test-heavy",
        ],
        timeout=300,
    )

    if code != 0 and "service" in stderr.lower():
        code, stdout, stderr = await _run_cmd(  # type: ignore[misc]
            [
                "docker",
                "compose",
                "-f",
                str(compose_path),
                "up",
                "--build",
                "--abort-on-container-exit",
            ],
            timeout=300,
        )

    return {
        "status": "completed" if code == 0 else "failed",
        "exit_code": code,
        "repo": repo_name,
        "output": stdout[-2000:] if stdout else "",
        "error": stderr[-1000:] if stderr else "",
    }
