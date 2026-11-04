"""Go-public prep part 8: ci-standard core jobs on hosted runners (#3303)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci-standard.yml"

ISSUE_JOBS = (
    "pick-runner",
    "changes",
    "ci-health-check",
    "lint",
    "security-scan",
    "tests",
    "tests-required",
    "quality-gate",
)


def test_ci_standard_issue_jobs_use_ubuntu_latest() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job_id in ISSUE_JOBS:
        assert data["jobs"][job_id]["runs-on"] == "ubuntu-latest", job_id
