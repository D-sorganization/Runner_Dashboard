from __future__ import annotations

import pytest
from routers import runs_workflows


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enrich_run_adds_runner_and_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_gh_api(path: str) -> dict:
        assert path == "/repos/D-sorganization/Runner_Dashboard/actions/runs/123/jobs"
        return {
            "jobs": [
                {
                    "id": 1,
                    "name": "tests",
                    "status": "completed",
                    "conclusion": "success",
                    "runner_name": "d-sorg-local-ControlTower-nvme-3",
                    "runner_id": 42,
                    "started_at": "2026-05-26T12:00:00Z",
                    "completed_at": "2026-05-26T12:02:00Z",
                }
            ]
        }

    monkeypatch.setattr(runs_workflows, "gh_api", fake_gh_api)
    enriched = await runs_workflows._enrich_run_with_job_placement(
        {
            "id": 123,
            "repository": {"name": "Runner_Dashboard"},
            "name": "tests",
        }
    )

    assert enriched["runner_name"] == "d-sorg-local-ControlTower-nvme-3"
    assert enriched["runner_names"] == ["d-sorg-local-ControlTower-nvme-3"]
    assert enriched["machine_name"] == "ControlTower-NVMe"


def _raw_github_run() -> dict:
    """A workflow run shaped like GitHub's REST payload (nested objects, API URLs)."""
    owner = {"login": "octo", "id": 7, "avatar_url": "https://a", "url": "https://api/u", "repos_url": "https://api/r"}
    repo = {
        "id": 1,
        "name": "Runner_Dashboard",
        "full_name": "D-sorganization/Runner_Dashboard",
        "html_url": "https://github.com/D-sorganization/Runner_Dashboard",
        "private": False,
        "owner": owner,
        "hooks_url": "https://api/h",
        "issues_url": "https://api/i",
    }
    return {
        "id": 123,
        "name": "ci",
        "status": "completed",
        "conclusion": "failure",
        "html_url": "https://github.com/run/123",
        "jobs_url": "https://api/jobs",
        "logs_url": "https://api/logs",
        "rerun_url": "https://api/rerun",
        "repository": repo,
        "head_repository": dict(repo),
        "actor": owner,
        "triggering_actor": dict(owner),
        "head_commit": {"id": "abc", "message": "fix", "author": {"name": "D", "email": "d@example.com"}},
    }


@pytest.mark.unit
def test_slim_run_drops_api_urls_and_nested_repo_noise() -> None:
    slim = runs_workflows._slim_run(_raw_github_run())

    assert slim["html_url"] == "https://github.com/run/123"
    assert not {"jobs_url", "logs_url", "rerun_url"} & slim.keys()
    assert slim["repository"] == {
        "id": 1,
        "name": "Runner_Dashboard",
        "full_name": "D-sorganization/Runner_Dashboard",
        "html_url": "https://github.com/D-sorganization/Runner_Dashboard",
        "private": False,
    }
    assert "head_repository" not in slim
    assert slim["actor"] == {"login": "octo", "id": 7, "avatar_url": "https://a"}
    assert slim["triggering_actor"]["login"] == "octo"
    assert slim["head_commit"] == {"id": "abc", "message": "fix"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enriched_runs_payload_is_slim(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_runs(request: object, per_page: int = 50) -> dict:
        return {"workflow_runs": [_raw_github_run()], "total_count": 1}

    async def fake_enrich(run: dict) -> dict:
        return {**run, "jobs": [], "runner_names": []}

    monkeypatch.setattr(runs_workflows, "get_runs", fake_get_runs)
    monkeypatch.setattr(runs_workflows, "_enrich_run_with_job_placement", fake_enrich)
    monkeypatch.setattr(runs_workflows, "should_proxy_fleet_to_hub", lambda request: False)
    monkeypatch.setattr(runs_workflows, "cache_get", lambda key, ttl: None)
    monkeypatch.setattr(runs_workflows, "cache_set", lambda key, value: None)

    data = await runs_workflows.get_enriched_runs(object(), per_page=1)

    (run,) = data["workflow_runs"]
    assert "jobs_url" not in run and "head_repository" not in run
    assert run["repository"]["name"] == "Runner_Dashboard"
    assert run["jobs"] == []
