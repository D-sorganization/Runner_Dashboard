"""Go-public prep part 10: PR/merge_group hosted routing on contract workflows (#3303)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

PR_SWITCH = "github.event_name == 'pull_request' && vars.CI_PR_RUNNER_MODE == 'hosted' && 'ubuntu-latest'"
QUEUE_SWITCH = "github.event_name == 'merge_group' && vars.CI_QUEUE_RUNNER_MODE == 'hosted' && 'ubuntu-latest'"

PART10 = (
    ("api-contract-regeneration.yml", "api-contract-regeneration"),
    ("ci-private-roles-redaction-gate.yml", "redaction-gate"),
    ("ci-standalone-smoke.yml", None),
)


def _runs_on(workflow: str, job_id: str | None) -> str:
    data = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    if job_id is None:
        job_id = next(iter(data["jobs"]))
    return " ".join(str(data["jobs"][job_id]["runs-on"]).split())


def test_part10_jobs_honor_pr_and_merge_group_hosted_modes() -> None:
    for workflow, job_id in PART10:
        runs_on = _runs_on(workflow, job_id)
        assert PR_SWITCH in runs_on, workflow
        assert QUEUE_SWITCH in runs_on, workflow
        assert "d-sorg-fleet" in runs_on, workflow
