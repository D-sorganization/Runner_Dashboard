"""Reserved-runner routing for required merge_group jobs (#3046, implements #2768).

Postcondition: when ``vars.CI_QUEUE_RUNNER_LABEL`` is set and
``CI_QUEUE_RUNNER_MODE`` is not ``hosted``, every merge_group job on the five
in-scope workflows resolves to that label instead of the shared
``d-sorg-fleet`` pool. No other event (``pull_request``, ``push``,
``schedule``, ``workflow_dispatch``) ever resolves to the label, regardless
of whether it is set. With the switch unset, routing is byte-identical to
current ``main``.

Harness: reuses ``_run_select`` from ``test_ci_expedite_runner_mode`` (the
``ci-standard.yml`` ``pick-runner``/``select`` bash harness) for ci-standard.yml,
and the plain substring-on-the-``runs-on``-expression style from
``test_ci_pr_runner_mode.py`` (``PR_SWITCH``/``QUEUE_SWITCH``) for the four
workflows whose ``runs-on`` is a bare GitHub Actions expression with no
pick-runner indirection. No new YAML-expression evaluator is introduced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from test_ci_expedite_runner_mode import _run_select

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# github.event_name == 'merge_group' && vars.CI_QUEUE_RUNNER_LABEL ...
QUEUE_LABEL_SWITCH = "github.event_name == 'merge_group' && vars.CI_QUEUE_RUNNER_LABEL"

# pick-runner's own ``runs-on`` already nests a ``CI_QUEUE_RUNNER_MODE ==
# 'hosted'`` ternary group for merge_group, so the new branch there carries
# an explicit ``!= 'hosted'`` guard instead of relying on branch order alone.
PICK_RUNNER_QUEUE_LABEL_SWITCH = (
    "github.event_name == 'merge_group' && vars.CI_QUEUE_RUNNER_MODE != 'hosted' && vars.CI_QUEUE_RUNNER_LABEL"
)

# The five in-scope workflows. ci-standard.yml routes through pick-runner's
# outputs (tested via the bash harness below); the other four resolve
# ``runs-on`` directly from a literal expression (tested textually).
QUEUE_LABEL_JOBS: dict[str, list[str]] = {
    "frontend-tests.yml": [
        "frontend-required",
        "frontend-scope",
        "typecheck",
        "vitest",
        "perf-budget",
        "playwright-e2e",
        "staff-console-e2e",
    ],
    "ci-secrets.yml": ["gitleaks", "detect-secrets"],
    "anti-phantom-merge.yml": ["guard"],
    "ci-auto-deploy-regression.yml": ["auto-deploy-regression"],
}


def _jobs(name: str, only: list[str]) -> dict[str, dict]:
    jobs = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]
    return {k: v for k, v in jobs.items() if k in only}


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "EVENT_NAME": "merge_group",
        "REPOSITORY_PRIVATE": "true",
        "HAS_EXPEDITE": "false",
        "FORCE_LOCAL": "false",
        "CI_QUEUE_RUNNER_MODE": "",
        "CI_QUEUE_RUNNER_LABEL": "",
        "CI_PR_RUNNER_MODE": "",
        "CONFIGURED_MODE": "",
        "FORK_PR": "false",
    }
    env.update(overrides)
    return env


# --- test_merge_group_jobs_use_queue_label_switch --------------------------


def test_ci_standard_merge_group_resolves_queue_label_when_set() -> None:
    code, outputs = _run_select(_base_env(CI_QUEUE_RUNNER_LABEL="d-sorg-fleet-queue"))
    assert code == 0
    assert outputs == {"runner": "d-sorg-fleet-queue", "mode": "queue-label"}


def test_ci_standard_pick_runner_job_runs_on_carries_queue_label_switch() -> None:
    job = _jobs("ci-standard.yml", ["pick-runner"])["pick-runner"]
    runs_on = " ".join(str(job["runs-on"]).split())
    assert PICK_RUNNER_QUEUE_LABEL_SWITCH in runs_on
    assert runs_on.endswith("'d-sorg-fleet')) }}")


def test_ci_standard_select_step_declares_queue_label_env() -> None:
    job = _jobs("ci-standard.yml", ["pick-runner"])["pick-runner"]
    select = next(step for step in job["steps"] if step.get("id") == "select")
    assert select["env"]["CI_QUEUE_RUNNER_LABEL"] == "${{ vars.CI_QUEUE_RUNNER_LABEL }}"


@pytest.mark.parametrize(("workflow", "only"), QUEUE_LABEL_JOBS.items())
def test_merge_group_jobs_use_queue_label_switch(workflow: str, only: list[str]) -> None:
    jobs = _jobs(workflow, only)
    assert jobs
    for job_id, job in jobs.items():
        runs_on = " ".join(str(job["runs-on"]).split())
        assert QUEUE_LABEL_SWITCH in runs_on, f"{workflow}::{job_id}"
        assert runs_on.endswith("'d-sorg-fleet' }}"), f"{workflow}::{job_id} lost the fleet default"


# --- test_pr_push_schedule_never_use_queue_label ----------------------------


@pytest.mark.parametrize("event_name", ["pull_request", "push", "schedule", "workflow_dispatch"])
def test_ci_standard_never_resolves_queue_label_for_non_merge_group(event_name: str) -> None:
    code, outputs = _run_select(_base_env(EVENT_NAME=event_name, CI_QUEUE_RUNNER_LABEL="d-sorg-fleet-queue"))
    assert code == 0
    assert outputs["runner"] != "d-sorg-fleet-queue"


@pytest.mark.parametrize(("workflow", "only"), QUEUE_LABEL_JOBS.items())
def test_queue_label_switch_is_gated_on_merge_group_only(workflow: str, only: list[str]) -> None:
    """Every occurrence of the switch is literally gated on merge_group.

    (A bare ``vars.CI_QUEUE_RUNNER_LABEL || 'd-sorg-fleet'`` with no event
    guard would let any event resolve to the label once it is set.)
    """
    for job_id, job in _jobs(workflow, only).items():
        runs_on = " ".join(str(job["runs-on"]).split())
        assert "vars.CI_QUEUE_RUNNER_LABEL" not in runs_on.replace(QUEUE_LABEL_SWITCH, ""), f"{workflow}::{job_id}"


# --- test_switch_unset_is_noop ----------------------------------------------


@pytest.mark.parametrize(
    "event_name",
    ["pull_request", "merge_group", "push", "schedule", "workflow_dispatch"],
)
def test_switch_unset_is_noop(event_name: str) -> None:
    """``CI_QUEUE_RUNNER_LABEL`` unset resolves every event exactly as current main does."""
    code, outputs = _run_select(_base_env(EVENT_NAME=event_name, CI_QUEUE_RUNNER_LABEL=""))
    assert code == 0
    # Private repo, no expedite, no force-local, no hosted overrides, no fork:
    # main resolves every event to the shared fleet label.
    assert outputs["runner"] == "d-sorg-fleet"


@pytest.mark.parametrize(("workflow", "only"), QUEUE_LABEL_JOBS.items())
def test_runs_on_unset_label_falls_back_to_fleet_text(workflow: str, only: list[str]) -> None:
    """Static sanity: the switch is additive — the bare fleet fallback string survives."""
    for job_id, job in _jobs(workflow, only).items():
        runs_on = " ".join(str(job["runs-on"]).split())
        assert runs_on.endswith("'d-sorg-fleet' }}"), f"{workflow}::{job_id}"


# --- test_hosted_and_expedite_keep_precedence -------------------------------


def test_hosted_mode_wins_over_queue_label_on_merge_group() -> None:
    code, outputs = _run_select(
        _base_env(CI_QUEUE_RUNNER_MODE="hosted", CI_QUEUE_RUNNER_LABEL="d-sorg-fleet-queue")
    )
    assert code == 0
    assert outputs == {"runner": "ubuntu-latest", "mode": "hosted-queue"}


def test_expedite_wins_over_queue_label_on_merge_group() -> None:
    code, outputs = _run_select(_base_env(HAS_EXPEDITE="true", CI_QUEUE_RUNNER_LABEL="d-sorg-fleet-queue"))
    assert code == 0
    assert outputs == {"runner": "ubuntu-latest", "mode": "hosted-expedite"}


def test_fork_pr_never_reaches_fleet_even_with_queue_label_set() -> None:
    code, outputs = _run_select(
        _base_env(
            EVENT_NAME="pull_request",
            FORK_PR="true",
            CI_QUEUE_RUNNER_LABEL="d-sorg-fleet-queue",
        )
    )
    assert code == 0
    assert outputs["runner"] == "ubuntu-latest"


def test_ci_standard_pick_runner_job_runs_on_hosted_mode_precedes_queue_label() -> None:
    job = _jobs("ci-standard.yml", ["pick-runner"])["pick-runner"]
    runs_on = " ".join(str(job["runs-on"]).split())
    hosted_idx = runs_on.index("vars.CI_QUEUE_RUNNER_MODE == 'hosted'")
    label_idx = runs_on.index(PICK_RUNNER_QUEUE_LABEL_SWITCH)
    assert hosted_idx < label_idx
