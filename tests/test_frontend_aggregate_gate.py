"""Execute the actual frontend aggregate gate against failed and skipped jobs."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.deploy.bash_host import require_bash

ROOT = Path(__file__).resolve().parents[1]
JOBS = ("typecheck", "vitest", "perf-budget", "playwright-e2e", "staff-console-e2e")
# Slim merge_group gate (#2891): these jobs are not scheduled; skipped is OK only there.
_MERGE_GROUP_HEAVY_SKIPS = frozenset({"perf-budget", "playwright-e2e", "staff-console-e2e"})


def _run_gate(
    *,
    frontend: bool,
    staff: bool,
    failure: tuple[str, str] | None = None,
    event_name: str = "pull_request",
) -> int:
    workflow = yaml.safe_load((ROOT / ".github/workflows/frontend-tests.yml").read_text())
    gate = workflow["jobs"]["frontend-required"]
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {"frontend-scope", *JOBS}
    needs = {job: {"result": "success"} for job in JOBS}
    if not frontend:
        for job in ("vitest", "perf-budget", "playwright-e2e"):
            needs[job]["result"] = "skipped"
    if not staff:
        needs["staff-console-e2e"]["result"] = "skipped"
    needs["frontend-scope"] = {
        "result": "success",
        "outputs": {
            "run_frontend_tests": str(frontend).lower(),
            "run_staff_e2e": str(staff).lower(),
        },
    }
    if failure:
        needs[failure[0]]["result"] = failure[1]
    env = {
        **os.environ,
        "NEEDS_JSON": json.dumps(needs),
        "GITHUB_EVENT_NAME": event_name,
    }
    return subprocess.run(
        [require_bash(), "-c", gate["steps"][0]["run"]],
        env=env,
        capture_output=True,
        check=False,
    ).returncode


@pytest.mark.parametrize("frontend,staff", [(True, True), (False, True), (False, False)])
def test_intentional_scope_skips_pass(frontend: bool, staff: bool) -> None:
    assert _run_gate(frontend=frontend, staff=staff, event_name="pull_request") == 0


@pytest.mark.parametrize("event_name", ["pull_request", "push", "merge_group"])
@pytest.mark.parametrize("job", ["frontend-scope", *JOBS])
@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
def test_required_job_failure_never_becomes_a_green_aggregate(
    event_name: str, job: str, result: str
) -> None:
    code = _run_gate(
        frontend=True,
        staff=True,
        failure=(job, result),
        event_name=event_name,
    )
    if (
        event_name == "merge_group"
        and result == "skipped"
        and job in _MERGE_GROUP_HEAVY_SKIPS
    ):
        assert code == 0, "merge_group slim gate: deliberate heavy-job skips must pass"
        return
    assert code != 0, f"{event_name} {job}={result} must not green the aggregate"


def test_gate_is_declared_in_required_policy_and_supplement() -> None:
    policy = json.loads((ROOT / "config/required_status_checks_policy.json").read_text())
    assert "frontend-required" in {entry["context"] for entry in policy["required_contexts"]}
    supplement = json.loads((ROOT / "config/repository_rulesets/required-status-checks-supplement.json").read_text())
    contexts = {
        check["context"] for rule in supplement["rules"] for check in rule["parameters"]["required_status_checks"]
    }
    assert "frontend-required" in contexts
