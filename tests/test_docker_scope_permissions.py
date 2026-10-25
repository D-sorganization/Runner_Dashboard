from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCKER_WORKFLOW = ROOT / ".github" / "workflows" / "docker-build.yml"


def _effective_permissions(workflow: dict, job: dict) -> dict:
    """Job permissions replace workflow permissions; otherwise they are inherited."""
    return job.get("permissions", workflow.get("permissions", {}))


def test_docker_scope_job_has_minimum_pr_files_api_permissions() -> None:
    workflow = yaml.safe_load(DOCKER_WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["decide-docker-scope"]

    permissions = _effective_permissions(workflow, job)

    assert permissions.get("contents") == "read"
    assert permissions.get("pull-requests") == "read"
    assert job.get("permissions") == {
        "contents": "read",
        "pull-requests": "read",
    }
