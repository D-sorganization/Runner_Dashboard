"""Go-public prep part 8: ci-standard core jobs on hosted runners (#3303)."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts import ci_runner_selector as selector

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci-standard.yml"

HOSTED_GLUE_JOBS = (
    "pick-runner",
    "changes",
    "ci-health-check",
    "lint",
    "security-scan",
)
FAST_CHECKS_JOBS = ("tests", "tests-required", "quality-gate")


def test_ci_standard_issue_jobs_use_ubuntu_latest() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job_id in HOSTED_GLUE_JOBS:
        assert data["jobs"][job_id]["runs-on"] == "ubuntu-latest", job_id
    fast_checks = selector.pick_runner_fast_checks_runs_on_expression().strip()
    for job_id in FAST_CHECKS_JOBS:
        assert str(data["jobs"][job_id]["runs-on"]).strip() == fast_checks, job_id
