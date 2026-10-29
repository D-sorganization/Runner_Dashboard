"""CI merge-queue hosted runner opt-in (vars.CI_QUEUE_RUNNER_MODE)."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import yaml
from bash_host import require_bash

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci-standard.yml"

# Jobs that must stay on d-sorg-fleet even when CI_QUEUE_RUNNER_MODE=hosted because
# they consume fleet-host or tailnet credentials. Empty today: merge_group lanes
# use only the default GITHUB_TOKEN (PR-only scope step) and public checkout.
MERGE_QUEUE_FLEET_PINNED_JOBS: frozenset[str] = frozenset()

# Credential-like workflow references that must not appear in ci-standard.yml.
_FORBIDDEN_CREDENTIAL_MARKERS = (
    "secrets.",
    "TAILSCALE",
    "HUB_FLEET_TOKEN",
    "SSH_PRIVATE",
    "FLEET_SSH",
)


def _load_workflow() -> dict:
    data = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    triggers = data.get(True) or data.get("on") or {}
    assert "merge_group" in triggers
    return data


def _pick_runner_runs_on() -> str:
    job = _load_workflow()["jobs"]["pick-runner"]
    return str(job["runs-on"])


def _select_step_script() -> str:
    steps = _load_workflow()["jobs"]["pick-runner"]["steps"]
    select = next(step for step in steps if step.get("id") == "select")
    return str(select["run"])


def _run_select(env: dict[str, str]) -> tuple[int, dict[str, str], str]:
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "github_output"
        summary_path = Path(tmp) / "step_summary"
        output_path.write_text("", encoding="utf-8")
        summary_path.write_text("", encoding="utf-8")
        full_env = {
            "PATH": os.environ.get("PATH", ""),
            "GITHUB_OUTPUT": str(output_path),
            "GITHUB_STEP_SUMMARY": str(summary_path),
            **env,
        }
        result = subprocess.run(
            [require_bash(), "--noprofile", "--norc", "-eo", "pipefail", "-c", _select_step_script()],
            env=full_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        outputs: dict[str, str] = {}
        for line in output_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                outputs[key] = value
        return result.returncode, outputs, summary_path.read_text(encoding="utf-8")


def test_pick_runner_runs_on_includes_merge_queue_hosted_clause() -> None:
    runs_on = _pick_runner_runs_on()
    assert "vars.CI_QUEUE_RUNNER_MODE == 'hosted'" in runs_on
    assert "github.event_name == 'merge_group'" in runs_on


def test_private_merge_group_hosted_selects_ubuntu_latest() -> None:
    code, outputs, summary = _run_select(
        {
            "REPOSITORY_PRIVATE": "true",
            "EVENT_NAME": "merge_group",
            "CI_QUEUE_RUNNER_MODE": "hosted",
            "CONFIGURED_MODE": "",
            "FORCE_LOCAL": "false",
            "FORK_PR": "false",
        }
    )
    assert code == 0
    assert outputs["runner"] == "ubuntu-latest"
    assert outputs["mode"] == "hosted-queue"
    assert "hosted-queue" in summary.lower() or "CI_QUEUE_RUNNER_MODE" in summary


def test_private_pull_request_hosted_stays_on_fleet() -> None:
    code, outputs, _ = _run_select(
        {
            "REPOSITORY_PRIVATE": "true",
            "EVENT_NAME": "pull_request",
            "CI_QUEUE_RUNNER_MODE": "hosted",
            "CONFIGURED_MODE": "",
            "FORCE_LOCAL": "false",
            "FORK_PR": "false",
        }
    )
    assert code == 0
    assert outputs["runner"] == "d-sorg-fleet"
    assert outputs["mode"] == "local"


def test_private_merge_group_without_variable_stays_on_fleet() -> None:
    code, outputs, _ = _run_select(
        {
            "REPOSITORY_PRIVATE": "true",
            "EVENT_NAME": "merge_group",
            "CI_QUEUE_RUNNER_MODE": "",
            "CONFIGURED_MODE": "",
            "FORCE_LOCAL": "false",
            "FORK_PR": "false",
        }
    )
    assert code == 0
    assert outputs["runner"] == "d-sorg-fleet"
    assert outputs["mode"] == "local"


def test_force_local_selects_fleet() -> None:
    code, outputs, _ = _run_select(
        {
            "REPOSITORY_PRIVATE": "false",
            "EVENT_NAME": "workflow_dispatch",
            "CI_QUEUE_RUNNER_MODE": "hosted",
            "CONFIGURED_MODE": "",
            "FORCE_LOCAL": "true",
            "FORK_PR": "false",
        }
    )
    assert code == 0
    assert outputs["runner"] == "d-sorg-fleet"
    assert outputs["mode"] == "local"


def test_fork_pr_selects_hosted_runner() -> None:
    code, outputs, _ = _run_select(
        {
            "REPOSITORY_PRIVATE": "true",
            "EVENT_NAME": "pull_request",
            "CI_QUEUE_RUNNER_MODE": "",
            "CONFIGURED_MODE": "",
            "FORCE_LOCAL": "false",
            "FORK_PR": "true",
        }
    )
    assert code == 0
    assert outputs["runner"] == "ubuntu-latest"
    assert outputs["mode"] == "hosted"


def test_merge_group_workflow_uses_no_repository_secrets() -> None:
    text = CI_WORKFLOW.read_text(encoding="utf-8").replace("ci-secrets.yml", "")
    assert "secrets." not in text
    for marker in _FORBIDDEN_CREDENTIAL_MARKERS:
        assert marker not in text, f"unexpected credential marker {marker!r} in ci-standard.yml"


def test_github_token_is_limited_to_pull_request_scope_step() -> None:
    workflow = _load_workflow()
    changes_steps = workflow["jobs"]["changes"]["steps"]
    token_steps = [
        step for step in changes_steps if "GH_TOKEN" in str(step.get("env", {})) or "github.token" in str(step)
    ]
    assert len(token_steps) == 1
    scope_step = token_steps[0]
    assert scope_step.get("if") == "github.event_name == 'pull_request'"
    assert scope_step.get("id") == "python-scope"


def test_fleet_pinned_jobs_force_local_runner_when_hosted_queue_enabled() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]
    for job_id in MERGE_QUEUE_FLEET_PINNED_JOBS:
        assert job_id in jobs
        runs_on = str(jobs[job_id]["runs-on"])
        assert "d-sorg-fleet" in runs_on
        assert "hosted-queue" in runs_on or "CI_QUEUE_RUNNER_MODE" in runs_on

    routed_jobs = {
        job_id
        for job_id, job in jobs.items()
        if job_id != "pick-runner" and "needs.pick-runner.outputs.runner" in str(job.get("runs-on", ""))
    }
    assert routed_jobs >= {
        "changes",
        "ci-health-check",
        "lint",
        "security-scan",
        "tests",
        "tests-required",
        "quality-gate",
    }
    assert routed_jobs.isdisjoint(MERGE_QUEUE_FLEET_PINNED_JOBS)


def test_hosted_merge_queue_preserves_required_check_context_names() -> None:
    workflow = _load_workflow()
    quality_gate = workflow["jobs"]["quality-gate"]
    tests_required = workflow["jobs"]["tests-required"]
    assert "quality-gate" in workflow["jobs"]
    assert "name" not in quality_gate
    assert tests_required["name"] == "tests"
    assert quality_gate["if"] == "always()"
    assert tests_required["if"] == "always()"
