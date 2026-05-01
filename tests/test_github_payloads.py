"""Tests for typed GitHub payload view-models (issue #407).

Verifies that:
1. Models parse correctly from realistic GitHub API shapes.
2. Unknown extra fields do not change handler behaviour (extra="ignore").
3. Convenience properties replace nested .get() chains.
4. GhJob.from_api_dict normalises mixed label formats.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))


# ---------------------------------------------------------------------------
# GhRepository
# ---------------------------------------------------------------------------


def test_gh_repository_basic() -> None:
    from models.github_payloads import GhRepository

    repo = GhRepository.model_validate({"id": 1, "name": "my-repo", "full_name": "org/my-repo"})
    assert repo.name == "my-repo"
    assert repo.full_name == "org/my-repo"


def test_gh_repository_extra_fields_ignored() -> None:
    from models.github_payloads import GhRepository

    repo = GhRepository.model_validate({"name": "x", "new_github_field": "some_value"})
    assert repo.name == "x"
    assert not hasattr(repo, "new_github_field")


def test_gh_repository_defaults() -> None:
    from models.github_payloads import GhRepository

    repo = GhRepository.model_validate({})
    assert repo.name == ""
    assert repo.default_branch == "main"


# ---------------------------------------------------------------------------
# GhWorkflowRun
# ---------------------------------------------------------------------------


def test_gh_workflow_run_basic() -> None:
    from models.github_payloads import GhWorkflowRun

    raw = {
        "id": 12345,
        "name": "CI Standard",
        "status": "queued",
        "conclusion": None,
        "repository": {"name": "MyRepo", "full_name": "org/MyRepo"},
        "triggering_actor": {"login": "bot-user"},
    }
    run = GhWorkflowRun.model_validate(raw)
    assert run.id == 12345
    assert run.status == "queued"
    assert run.repository_name == "MyRepo"
    assert run.triggering_login == "bot-user"


def test_gh_workflow_run_repository_name_missing_repo() -> None:
    """repository_name returns '' when repository is absent."""
    from models.github_payloads import GhWorkflowRun

    run = GhWorkflowRun.model_validate({"id": 1})
    assert run.repository_name == ""


def test_gh_workflow_run_extra_fields_do_not_break() -> None:
    """Adding an unknown GitHub field must not change handler behaviour."""
    from models.github_payloads import GhWorkflowRun

    run = GhWorkflowRun.model_validate({"id": 99, "brand_new_github_field": "oops"})
    assert run.id == 99
    assert not hasattr(run, "brand_new_github_field")


def test_gh_workflow_run_triggering_login_absent() -> None:
    from models.github_payloads import GhWorkflowRun

    run = GhWorkflowRun.model_validate({"id": 5})
    assert run.triggering_login == ""


# ---------------------------------------------------------------------------
# GhRunner
# ---------------------------------------------------------------------------


def test_gh_runner_label_names() -> None:
    from models.github_payloads import GhRunner

    raw = {
        "id": 7,
        "name": "runner-1",
        "status": "online",
        "busy": False,
        "labels": [{"id": 1, "name": "self-hosted"}, {"id": 2, "name": "Linux"}],
    }
    runner = GhRunner.model_validate(raw)
    assert runner.label_names == ["self-hosted", "Linux"]
    assert runner.is_online is True


def test_gh_runner_extra_field_ignored() -> None:
    from models.github_payloads import GhRunner

    runner = GhRunner.model_validate({"id": 3, "status": "offline", "unknown": "val"})
    assert runner.status == "offline"
    assert not hasattr(runner, "unknown")


# ---------------------------------------------------------------------------
# GhJob
# ---------------------------------------------------------------------------


def test_gh_job_from_api_dict_dict_labels() -> None:
    """GhJob.from_api_dict normalises labels that are dicts."""
    from models.github_payloads import GhJob

    raw = {"id": 1, "run_id": 2, "name": "build", "status": "completed", "labels": [{"name": "ubuntu-latest"}]}
    job = GhJob.from_api_dict(raw)
    assert job.labels == ["ubuntu-latest"]


def test_gh_job_from_api_dict_string_labels() -> None:
    """GhJob.from_api_dict normalises labels that are already strings."""
    from models.github_payloads import GhJob

    raw = {"id": 2, "run_id": 3, "name": "test", "status": "queued", "labels": ["self-hosted", "windows"]}
    job = GhJob.from_api_dict(raw)
    assert job.labels == ["self-hosted", "windows"]


# ---------------------------------------------------------------------------
# Integration: queue router source uses GhWorkflowRun
# ---------------------------------------------------------------------------


def test_queue_router_imports_gh_workflow_run() -> None:
    source = (_BACKEND_DIR / "routers" / "queue.py").read_text(encoding="utf-8")
    assert "GhWorkflowRun" in source, "queue.py must import GhWorkflowRun"
    assert "r.repository_name" in source or "run.repository_name" in source


def test_runs_workflows_router_imports_models() -> None:
    source = (_BACKEND_DIR / "routers" / "runs_workflows.py").read_text(encoding="utf-8")
    assert "GhWorkflowRun" in source
    assert "GhJob" in source


def test_heavy_tests_router_imports_models() -> None:
    source = (_BACKEND_DIR / "routers" / "heavy_tests.py").read_text(encoding="utf-8")
    assert "GhWorkflowRun" in source


def test_runner_helpers_imports_gh_runner() -> None:
    source = (_BACKEND_DIR / "routers" / "runner_helpers.py").read_text(encoding="utf-8")
    assert "GhRunner" in source
