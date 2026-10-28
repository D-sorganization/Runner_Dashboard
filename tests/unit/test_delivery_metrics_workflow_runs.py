"""Actions workflow run drill-down (#2117 gap 4)."""

from __future__ import annotations

from datetime import UTC, datetime

from delivery_metrics.models import CheckRunSnapshot
from delivery_metrics.parse import parse_check_runs
from delivery_metrics.workflow_runs import build_workflow_run_drill_down, workflow_run_id_from_url

REPO = "D-sorganization/Runner_Dashboard"
HEAD = "abc"


def test_workflow_run_id_from_actions_details_url() -> None:
    url = "https://github.com/D-sorganization/Runner_Dashboard/actions/runs/39694764174/job/119551041354"
    assert workflow_run_id_from_url(url) == 39694764174
    assert workflow_run_id_from_url("https://example.invalid/runs/1") is None


def test_parse_check_runs_sets_workflow_run_id() -> None:
    payload = {
        "check_runs": [
            {
                "id": 1,
                "name": "tests",
                "status": "completed",
                "conclusion": "success",
                "head_sha": HEAD,
                "started_at": "2026-10-21T23:39:47Z",
                "completed_at": "2026-10-21T23:43:51Z",
                "details_url": "https://github.com/D-sorganization/Runner_Dashboard/actions/runs/39694764174/job/119550996263",
                "app": {"slug": "github-actions"},
            }
        ]
    }
    runs = parse_check_runs(REPO, HEAD, payload)
    assert runs[0].workflow_run_id == 39694764174


def test_build_workflow_run_drill_down_groups_jobs() -> None:
    t = datetime(2026, 10, 21, tzinfo=UTC)
    runs = (
        CheckRunSnapshot(
            repo_full=REPO,
            head_sha=HEAD,
            check_run_id=10,
            name="tests",
            status="completed",
            conclusion="success",
            started_at=t,
            completed_at=t,
            details_url="https://github.com/D-sorganization/Runner_Dashboard/actions/runs/99/job/10",
            app_slug="github-actions",
            workflow_run_id=99,
        ),
        CheckRunSnapshot(
            repo_full=REPO,
            head_sha=HEAD,
            check_run_id=11,
            name="lint",
            status="completed",
            conclusion="failure",
            started_at=t,
            completed_at=t,
            details_url="https://github.com/D-sorganization/Runner_Dashboard/actions/runs/99/job/11",
            app_slug="github-actions",
            workflow_run_id=99,
        ),
    )
    body = build_workflow_run_drill_down(runs)
    assert len(body) == 1
    assert body[0]["workflow_run_id"] == 99
    assert len(body[0]["jobs"]) == 2
    assert "actions/runs/99" in body[0]["html_url"]
