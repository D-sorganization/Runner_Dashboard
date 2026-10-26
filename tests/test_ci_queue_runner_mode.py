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
