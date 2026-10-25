"""CI Standard: superseded-cancel neutrality is pull_request-only (#2764)."""

from __future__ import annotations

from pathlib import Path

import yaml

_CI_STANDARD = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci-standard.yml"


def _step_run(job_id: str, step_name: str) -> str:
    data = yaml.safe_load(_CI_STANDARD.read_text(encoding="utf-8"))
    for step in data["jobs"][job_id]["steps"]:
        if step.get("name") == step_name:
            run = step.get("run")
            assert isinstance(run, str)
            return run
    raise AssertionError(f"missing step {step_name} in {job_id}")


def test_tests_required_checks_pr_head_before_neutral_cancel() -> None:
    run = _step_run("tests-required", "Confirm Python test matrix")
    assert "RUN_HEAD_SHA" in run and "PR_HEAD_SHA" in run
    assert "current PR head" in run
    assert "merge_group must not merge untested" in run


def test_quality_gate_checks_pr_head_before_neutral_cancel() -> None:
    run = _step_run("quality-gate", "Require every gated job to succeed")
    assert "RUN_HEAD_SHA" in run and "PR_HEAD_SHA" in run
    assert "merge_queue_lane" in run
    assert "cannot merge untested" in run
