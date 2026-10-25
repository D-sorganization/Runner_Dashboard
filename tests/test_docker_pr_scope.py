"""Docker PR scope detection — git diff, not the PR files API (contents: read only)."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.ci.docker_pr_scope import run_docker_for_paths

ROOT = Path(__file__).resolve().parents[1]
DOCKER_WORKFLOW = ROOT / ".github" / "workflows" / "docker-build.yml"


def test_run_docker_for_backend_change() -> None:
    assert run_docker_for_paths(["backend/server.py"])


def test_run_docker_for_dockerfile() -> None:
    assert run_docker_for_paths(["Dockerfile"])


def test_skip_docs_only_pr() -> None:
    assert not run_docker_for_paths(["docs/architecture.md", "README.md"])


def test_truncated_file_list_runs_docker() -> None:
    assert run_docker_for_paths([f"docs/file{i}.md" for i in range(100)])


def test_docker_scope_workflow_does_not_call_pull_files_api() -> None:
    workflow = yaml.safe_load(DOCKER_WORKFLOW.read_text(encoding="utf-8"))
    blob = yaml.dump(workflow)
    assert "/pulls/" not in blob
    assert "pulls/{pr_number}/files" not in blob


def test_arm_cap_reconcile_uses_python_311_or_newer() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "arm-cap-reconcile.yml").read_text(encoding="utf-8")
    )
    run_step = next(s for s in workflow["jobs"]["reconcile"]["steps"] if s.get("name") == "Reconcile armed inventory")
    script = run_step["run"]
    assert "python3 scripts/arm_cap_reconcile.py" not in script
    assert "python3.11" in script or "python3.12" in script


def test_redundant_pr_closer_concurrency_scoped_per_pull_request() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "Agent-Redundant-PR-Closer.yml").read_text(encoding="utf-8")
    )
    group = workflow["concurrency"]["group"]
    assert "pull_request.number" in group or "github.run_id" in group
