from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCKER_WORKFLOW = ROOT / ".github" / "workflows" / "docker-build.yml"


def _effective_permissions(workflow: dict, job: dict) -> dict:
    """Job permissions replace workflow permissions; otherwise they are inherited."""
    return job.get("permissions", workflow.get("permissions", {}))


def test_docker_scope_job_uses_git_diff_not_pr_files_api() -> None:
    workflow = yaml.safe_load(DOCKER_WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["decide-docker-scope"]
    steps_blob = yaml.dump(job.get("steps", []))
    assert "git diff --name-only" in steps_blob
    assert "docker_pr_scope.py" in steps_blob
    assert "/pulls/" not in steps_blob
    assert job.get("permissions") is None
