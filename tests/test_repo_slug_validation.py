from __future__ import annotations

import pytest
import security
import server
from fastapi import HTTPException
from fastapi.testclient import TestClient
from routers import assistant, remediation


def test_validate_repo_slug_accepts_github_repo_names() -> None:
    assert security.validate_repo_slug("Runner_Dashboard") == "Runner_Dashboard"
    assert security.validate_repo_slug("repo.name_1-2") == "repo.name_1-2"


@pytest.mark.parametrize("repo", ["abc&def", "../etc", "", "a" * 200])
def test_validate_repo_slug_rejects_path_and_query_injection(repo: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        security.validate_repo_slug(repo)

    assert exc_info.value.status_code == 422


@pytest.mark.parametrize(
    "normalizer",
    [
        server._normalize_repository_input,
        assistant._normalize_repository_input,
        remediation._normalize_repository_input,
    ],
)
def test_repository_normalizers_reuse_slug_validation(normalizer) -> None:
    assert normalizer("Runner_Dashboard") == ("Runner_Dashboard", "D-sorganization/Runner_Dashboard")
    assert normalizer("D-sorganization/Runner_Dashboard") == (
        "Runner_Dashboard",
        "D-sorganization/Runner_Dashboard",
    )

    with pytest.raises(HTTPException) as exc_info:
        normalizer("D-sorganization/../etc")

    assert exc_info.value.status_code == 422


@pytest.mark.parametrize("repo", ["abc&def", "../etc", "", "a" * 200])
def test_workflow_dispatch_rejects_invalid_repository_before_gh_api(
    repo: str, monkeypatch, mock_auth  # noqa: ARG001
) -> None:
    """Input validation (422) fires before dispatching — mock_auth prevents 401 masking it."""
    from routers import runs_workflows  # noqa: PLC0415

    async def fail_run_cmd(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("invalid repository reached gh api")

    monkeypatch.setattr(runs_workflows, "run_cmd", fail_run_cmd)
    client = TestClient(server.app, headers={"X-Requested-With": "XMLHttpRequest"})

    response = client.post(
        "/api/workflows/dispatch",
        json={"repository": repo, "workflow_id": "ci.yml", "ref": "main", "inputs": {}},
    )

    assert response.status_code == 422


def test_cancel_run_rejects_invalid_path_repository_before_gh_api(
    monkeypatch, mock_auth  # noqa: ARG001
) -> None:
    """Input validation (422) fires before dispatching — mock_auth prevents 401 masking it."""
    from routers import queue  # noqa: PLC0415

    async def fail_run_cmd(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("invalid repository reached gh api")

    monkeypatch.setattr(queue, "run_cmd", fail_run_cmd)
    client = TestClient(server.app, headers={"X-Requested-With": "XMLHttpRequest"})

    response = client.post("/api/runs/abc%26def/cancel/123")

    assert response.status_code == 422
