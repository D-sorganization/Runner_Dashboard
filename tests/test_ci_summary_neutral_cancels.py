"""CI Standard summary jobs treat concurrency-cancelled inputs as neutral."""

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


def test_quality_gate_treats_cancelled_dependencies_as_neutral() -> None:
    run = _step_run("quality-gate", "Require every gated job to succeed")
    assert "cancelled" in run and "superseded" in run.lower()


def test_tests_required_treats_cancelled_ci_health_as_neutral() -> None:
    run = _step_run("tests-required", "Confirm Python test matrix")
    assert "cancelled" in run and "ci-health-check" in run
