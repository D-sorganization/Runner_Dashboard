"""Unit tests for queue_job_diagnostic_bridge (#2897)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from queue_job_diagnostic_bridge import (  # noqa: E402
    build_wait_reason_classification,
    jobs_for_classification,
    policy_provisioning_snapshot,
)

pytestmark = pytest.mark.unit


def test_jobs_for_classification_maps_diagnose_samples() -> None:
    mapped = jobs_for_classification(
        [
            {
                "repo": "Runner_Dashboard",
                "run_id": 99,
                "workflow": "Frontend Tests",
                "job": "Playwright E2E smoke tests",
                "labels": ["d-sorg-fleet-public-heavy"],
            }
        ]
    )
    assert mapped[0]["repo"] == "Runner_Dashboard"
    assert mapped[0]["labels"] == ["d-sorg-fleet-public-heavy"]


def test_build_wait_reason_classification_public_heavy_mismatch() -> None:
    runners = [
        {
            "id": 1,
            "status": "online",
            "busy": False,
            "labels": [{"name": "self-hosted"}, {"name": "d-sorg-fleet"}],
        }
    ]
    sampled = [
        {
            "repo": "Runner_Dashboard",
            "run_id": 1,
            "workflow": "Frontend Tests",
            "job": "Playwright E2E smoke tests",
            "labels": ["d-sorg-fleet-public-heavy"],
        }
    ]
    report = build_wait_reason_classification(sampled_jobs=sampled, runners=runners, runner_groups={})
    assert report is not None
    assert report["classification_counts"]["label_mismatch"] == 1


def test_policy_provisioning_snapshot_reads_public_heavy_config() -> None:
    snap = policy_provisioning_snapshot([])
    assert snap["group_label"] == "d-sorg-fleet-public-heavy"
    assert snap["online_runners_with_label"] == 0
    assert snap["workflow_jobs"]


def test_incomplete_runner_groups_skips_access_check() -> None:
    findings: list[str] = []
    groups = {
        "runner_groups": [{"name": "d-sorg-fleet-public-heavy", "visibility": "selected"}],
    }
    from queue_job_diagnostic_bridge import _runner_groups_for_classification  # noqa: PLC0415

    assert _runner_groups_for_classification(groups, findings) == {}
    assert findings
