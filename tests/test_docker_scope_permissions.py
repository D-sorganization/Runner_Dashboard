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


def test_docker_image_job_smoke_tests_before_trusting_the_build() -> None:
    """#3178: a build that cannot start must not reach docker-build-scan green."""
    workflow = yaml.safe_load(DOCKER_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["docker-image"]["steps"]
    by_id = {step.get("id"): step for step in steps if step.get("id")}

    assert "smoke" in by_id, "docker-image job must run the smoke test step"
    smoke = by_id["smoke"]
    assert smoke["if"] == "steps.build.outcome == 'success'"
    assert "docker_smoke_test.sh" in smoke["run"]

    build_index = steps.index(by_id["build"])
    smoke_index = steps.index(smoke)
    assert build_index < smoke_index, "smoke test must run after the image is built"
