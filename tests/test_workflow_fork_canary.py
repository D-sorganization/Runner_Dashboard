"""Tests for the fork-PR / self-hosted workflow canary (issue #3080)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
import workflow_fork_canary as wc  # noqa: E402

REAL = _ROOT / ".github" / "workflows"


def _job(**job: object) -> dict:
    return {"on": {"pull_request": {}}, "jobs": {"j": {"steps": [], **job}}}


def test_real_workflows_pass() -> None:
    assert wc.check_dir(REAL) == []


def test_seeded_unsafe_trigger_in_real_tree_fails(tmp_path: Path) -> None:
    shutil.copytree(REAL, tmp_path / "wf")
    unsafe = {"on": {"pull_request": {}}, "jobs": {"build": {"runs-on": ["self-hosted"], "steps": [{"run": "true"}]}}}
    (tmp_path / "wf" / "zz-unsafe.yml").write_text(yaml.safe_dump(unsafe), encoding="utf-8")
    assert [v.render() for v in wc.check_dir(tmp_path / "wf")] == ["zz-unsafe.yml:build: fork-pr-self-hosted"]
    assert wc.main(["--workflows", str(tmp_path / "wf"), "--mode", "enforce"]) == 1
    assert wc.main(["--workflows", str(tmp_path / "wf"), "--mode", "report"]) == 0


_FORK = "github.event.pull_request.head.repo.full_name"


@pytest.mark.parametrize(
    ("job", "unsafe"),
    [
        ({"runs-on": "ubuntu-latest"}, False),
        ({"runs-on": {"group": "fleet"}}, True),
        ({"runs-on": "fleet-label"}, True),
        ({"runs-on": "fleet-label", "if": f"{_FORK} == github.repository"}, False),
        ({"runs-on": "fleet-label", "if": "github.event.pull_request.user.login == 'dependabot[bot]'"}, False),
        ({"runs-on": f"${{{{ {_FORK} != github.repository && 'ubuntu-latest' || 'fleet-label' }}}}"}, False),
        ({"runs-on": f"${{{{ {_FORK} != github.repository && 'fleet-label' || 'ubuntu-latest' }}}}"}, True),
        ({"runs-on": "fleet-label", "environment": "heavy"}, False),
    ],
)
def test_pull_request_job_rules(job: dict, unsafe: bool) -> None:
    assert bool(wc.check_workflow("w", _job(**job))) is unsafe


def test_pull_request_target_checkout_of_pr_code_fails() -> None:
    step = {"uses": "actions/checkout@v4", "with": {"ref": "${{ github.event.pull_request.head.sha }}"}}
    doc = {"on": {"pull_request_target": {}}, "jobs": {"j": {"runs-on": "ubuntu-latest", "steps": [step]}}}
    assert [v.category for v in wc.check_workflow("w", doc)] == ["prt-checkout-pr-code"]


def test_yaml_on_parsed_as_true_is_handled() -> None:
    doc = yaml.safe_load("on: pull_request\njobs:\n  j:\n    runs-on: self-hosted\n    steps: []\n")
    assert wc.check_workflow("w", doc)
