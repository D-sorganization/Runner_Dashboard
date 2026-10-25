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


def test_tests_required_neutral_cancel_only_on_pull_request() -> None:
    run = _step_run("tests-required", "Confirm Python test matrix")
    assert "pr_supersession_lane" in run
    assert 'GITHUB_EVENT_NAME:-}" = "pull_request"' in run or '"pull_request"' in run
    assert "merge_group must not merge untested" in run


def test_tests_required_does_not_unconditionally_pass_cancelled() -> None:
    run = _step_run("tests-required", "Confirm Python test matrix")
    assert "handle_cancelled_upstream" in run
    assert "pr_supersession_lane && exit 0" in run


def test_quality_gate_neutral_cancel_only_on_pull_request() -> None:
    run = _step_run("quality-gate", "Require every gated job to succeed")
    assert "pr_supersession_lane" in run
    assert "superseded by a newer workflow run" in run.lower()


def test_quality_gate_merge_group_fails_skipped_or_cancelled_inputs() -> None:
    run = _step_run("quality-gate", "Require every gated job to succeed")
    assert "merge_queue_lane" in run
    assert "cannot merge untested" in run
    assert "reported 'skipped' on merge_group" in run
