"""priority:expedite routes PR and merge_group CI to hosted runners (#2952)."""

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
    return data


def _select_script() -> str:
    steps = _load_workflow()["jobs"]["pick-runner"]["steps"]
    select = next(step for step in steps if step.get("id") == "select")
    return str(select["run"])


def _run_select(env: dict[str, str]) -> tuple[int, dict[str, str]]:
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
            [require_bash(), "--noprofile", "--norc", "-eo", "pipefail", "-c", _select_script()],
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
        return result.returncode, outputs


def test_private_expedite_pull_request_uses_hosted() -> None:
    code, outputs = _run_select(
        {
            "REPOSITORY_PRIVATE": "true",
            "EVENT_NAME": "pull_request",
            "HAS_EXPEDITE": "true",
            "CI_QUEUE_RUNNER_MODE": "",
            "CONFIGURED_MODE": "",
            "FORCE_LOCAL": "false",
            "FORK_PR": "false",
        }
    )
    assert code == 0
    assert outputs["runner"] == "ubuntu-latest"
    assert outputs["mode"] == "hosted-expedite"


def test_private_expedite_merge_group_uses_hosted() -> None:
    code, outputs = _run_select(
        {
            "REPOSITORY_PRIVATE": "true",
            "EVENT_NAME": "merge_group",
            "HAS_EXPEDITE": "true",
            "CI_QUEUE_RUNNER_MODE": "",
            "CONFIGURED_MODE": "",
            "FORCE_LOCAL": "false",
            "FORK_PR": "false",
        }
    )
    assert code == 0
    assert outputs["runner"] == "ubuntu-latest"
    assert outputs["mode"] == "hosted-expedite"


def test_expedite_does_not_override_force_local() -> None:
    code, outputs = _run_select(
        {
            "REPOSITORY_PRIVATE": "true",
            "EVENT_NAME": "pull_request",
            "HAS_EXPEDITE": "true",
            "FORCE_LOCAL": "true",
            "CI_QUEUE_RUNNER_MODE": "",
            "CONFIGURED_MODE": "",
            "FORK_PR": "false",
        }
    )
    assert code == 0
    assert outputs["runner"] == "d-sorg-fleet"


def test_workflow_defines_detect_expedite_step() -> None:
    steps = _load_workflow()["jobs"]["pick-runner"]["steps"]
    assert any(step.get("id") == "detect-expedite" for step in steps)
