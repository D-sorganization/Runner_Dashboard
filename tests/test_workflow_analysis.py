from __future__ import annotations

import pytest
import workflow_analysis as wa


@pytest.mark.unit
@pytest.mark.parametrize(
    ("runner_name", "expected"),
    [
        ("d-sorg-local-node-a-nvme-1", "node-a-NVMe"),
        ("d-sorg-local-node-a-ssd-8", "node-a-SSD"),
        ("d-sorg-local-hub-a-4", "hub-a"),
        ("d-sorg-local-node-b-2", "node-b"),
        ("ubuntu-latest", "GitHub Hosted"),
        ("custom-runner", "custom-runner"),
        (None, None),
    ],
)
def test_infer_machine_from_runner_name(runner_name: str | None, expected: str | None) -> None:
    assert wa.infer_machine_from_runner_name(runner_name) == expected


@pytest.mark.unit
def test_run_duration_seconds_rejects_negative_duration() -> None:
    assert (
        wa.run_duration_seconds(
            {
                "run_started_at": "2026-05-26T12:10:00Z",
                "updated_at": "2026-05-26T12:00:00Z",
            }
        )
        is None
    )


@pytest.mark.unit
def test_summarize_runs_by_workflow_and_machine_builds_matrix() -> None:
    runs = [
        {
            "name": "CI",
            "repository": {"name": "Runner_Dashboard"},
            "conclusion": "success",
            "machine_name": "node-a-NVMe",
            "run_started_at": "2026-05-26T12:00:00Z",
            "updated_at": "2026-05-26T12:02:00Z",
        },
        {
            "name": "CI",
            "repository": {"name": "Runner_Dashboard"},
            "conclusion": "failure",
            "jobs": [{"runner_name": "d-sorg-local-node-a-ssd-1"}],
            "run_started_at": "2026-05-26T13:00:00Z",
            "updated_at": "2026-05-26T13:05:00Z",
        },
        {
            "name": "Lint",
            "repository": {"name": "Runner_Dashboard"},
            "conclusion": "cancelled",
            "runner_name": "d-sorg-local-hub-a-1",
        },
    ]

    summary = wa.summarize_runs_by_workflow_and_machine(runs)

    assert summary["sample_size"] == 3
    assert summary["success_rate"] == pytest.approx(33.3)
    assert summary["failure_reasons"] == {"failure": 1, "cancelled": 1}
    machines = {row["machine_name"]: row for row in summary["machines"]}
    assert machines["node-a-NVMe"]["success"] == 1
    assert machines["node-a-SSD"]["failure"] == 1
    assert machines["hub-a"]["cancelled"] == 1
    matrix = {(row["workflow_name"], row["machine_name"]): row for row in summary["matrix"]}
    assert matrix[("CI", "node-a-NVMe")]["avg_duration_seconds"] == 120
    assert matrix[("CI", "node-a-SSD")]["avg_duration_seconds"] == 300
