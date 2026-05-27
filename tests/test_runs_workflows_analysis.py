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
