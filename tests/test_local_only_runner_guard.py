"""Tests for scripts/local_only_runner_guard.py."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from scripts import local_only_runner_guard as guard

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def test_repo_workflows_pass_hosted_job_guard() -> None:
    violations = guard.audit_workflows(WORKFLOWS)
    assert violations == [], "\n".join(violations)


def test_non_allowlisted_job_cannot_use_fast_checks_variable(tmp_path: Path) -> None:
    policy_src = REPO_ROOT / "config" / "required_status_checks_policy.json"
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    config_dir.joinpath("required_status_checks_policy.json").write_text(
        policy_src.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci-standard.yml").write_text(
        dedent(
            """
            on: pull_request
            jobs:
              lint:
                runs-on: ${{ vars.RD_FAST_CHECKS_RUNNER == 'hosted' && 'ubuntu-latest' || 'd-sorg-fleet' }}
            """
        ),
        encoding="utf-8",
    )
    violations = guard.audit_workflows(wf_dir)
    assert any("lint" in item for item in violations)
