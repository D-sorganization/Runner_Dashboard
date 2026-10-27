"""CI_PR_RUNNER_MODE=hosted routes private-repo PR checks to GitHub-hosted runners.

Parallel to CI_QUEUE_RUNNER_MODE (merge_group only). Default stays d-sorg-fleet.
Heavy jobs (Playwright / Staff Console e2e, Docker builds, deploy regression)
stay on the local fleet per the 2026-10-25 hybrid runner policy.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from test_ci_expedite_runner_mode import _run_select

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PR_SWITCH = "github.event_name == 'pull_request' && vars.CI_PR_RUNNER_MODE == 'hosted' && 'ubuntu-latest'"

LIGHT_JOBS = {
    "ci-standard.yml": ["pick-runner"],
    "frontend-tests.yml": ["frontend-required", "frontend-scope", "typecheck", "vitest", "perf-budget"],
    "ci-secrets.yml": None,
    "anti-phantom-merge.yml": None,
    "ci-spec-check.yml": None,
    "lint-workflow-files.yml": None,
    "architecture-map-contract.yml": None,
    "local-only-runner-guard.yml": None,
}
HEAVY_JOBS = {
    "frontend-tests.yml": ["playwright-e2e", "staff-console-e2e"],
    "docker-build.yml": None,
    "ci-auto-deploy-regression.yml": None,
}


def _jobs(name: str, only: list[str] | None) -> dict[str, dict]:
    jobs = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]
    return {k: v for k, v in jobs.items() if only is None or k in only}


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "EVENT_NAME": "pull_request",
        "REPOSITORY_PRIVATE": "true",
        "HAS_EXPEDITE": "false",
        "FORCE_LOCAL": "false",
        "CI_QUEUE_RUNNER_MODE": "",
        "CI_PR_RUNNER_MODE": "",
        "CONFIGURED_MODE": "",
        "FORK_PR": "false",
    }
    env.update(overrides)
    return env


def test_private_pull_request_hosted_when_pr_mode_hosted() -> None:
    code, outputs = _run_select(_base_env(CI_PR_RUNNER_MODE="hosted"))
    assert code == 0
    assert outputs == {"runner": "ubuntu-latest", "mode": "hosted-pr"}


def test_private_pull_request_defaults_to_fleet() -> None:
    code, outputs = _run_select(_base_env())
    assert code == 0
    assert outputs["runner"] == "d-sorg-fleet"


def test_pr_mode_does_not_affect_merge_group() -> None:
    code, outputs = _run_select(_base_env(EVENT_NAME="merge_group", CI_PR_RUNNER_MODE="hosted"))
    assert code == 0
    assert outputs["runner"] == "d-sorg-fleet"


def test_force_local_beats_pr_mode() -> None:
    code, outputs = _run_select(_base_env(CI_PR_RUNNER_MODE="hosted", FORCE_LOCAL="true"))
    assert code == 0
    assert outputs["runner"] == "d-sorg-fleet"


def test_pick_runner_passes_pr_mode_to_resolver() -> None:
    job = _jobs("ci-standard.yml", ["pick-runner"])["pick-runner"]
    select = next(step for step in job["steps"] if step.get("id") == "select")
    assert select["env"]["CI_PR_RUNNER_MODE"] == "${{ vars.CI_PR_RUNNER_MODE }}"


@pytest.mark.parametrize(("workflow", "only"), LIGHT_JOBS.items())
def test_light_pr_jobs_honor_pr_runner_mode(workflow: str, only: list[str] | None) -> None:
    jobs = _jobs(workflow, only)
    assert jobs
    for job_id, job in jobs.items():
        runs_on = " ".join(str(job["runs-on"]).split())
        assert PR_SWITCH in runs_on, f"{workflow}::{job_id}"
        assert "d-sorg-fleet" in runs_on, f"{workflow}::{job_id} lost the fleet default"


@pytest.mark.parametrize(("workflow", "only"), HEAVY_JOBS.items())
def test_heavy_jobs_stay_local(workflow: str, only: list[str] | None) -> None:
    for job_id, job in _jobs(workflow, only).items():
        assert "CI_PR_RUNNER_MODE" not in str(job.get("runs-on")), f"{workflow}::{job_id}"


QUEUE_SWITCH = "github.event_name == 'merge_group' && vars.CI_QUEUE_RUNNER_MODE == 'hosted' && 'ubuntu-latest'"
QUEUE_LIGHT_JOBS = {
    "frontend-tests.yml": ["frontend-required", "frontend-scope", "typecheck", "vitest", "perf-budget"],
    "ci-secrets.yml": None,
    "architecture-map-contract.yml": None,
}


@pytest.mark.parametrize(("workflow", "only"), QUEUE_LIGHT_JOBS.items())
def test_light_queue_jobs_honor_queue_runner_mode(workflow: str, only: list[str] | None) -> None:
    jobs = _jobs(workflow, only)
    assert jobs
    for job_id, job in jobs.items():
        runs_on = " ".join(str(job["runs-on"]).split())
        assert QUEUE_SWITCH in runs_on, f"{workflow}::{job_id}"
        assert runs_on.endswith("'d-sorg-fleet' }}"), f"{workflow}::{job_id} lost the fleet default"


@pytest.mark.parametrize(("workflow", "only"), HEAVY_JOBS.items())
def test_heavy_jobs_ignore_queue_runner_mode(workflow: str, only: list[str] | None) -> None:
    for job_id, job in _jobs(workflow, only).items():
        assert "CI_QUEUE_RUNNER_MODE" not in str(job.get("runs-on")), f"{workflow}::{job_id}"
