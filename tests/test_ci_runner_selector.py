"""Tests for scripts/ci_runner_selector.py (hybrid required-check hosted routing)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import ci_runner_selector as selector

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("value", ["hosted", "local", "", None])
def test_assert_valid_rd_fast_checks_accepts_contract_values(value: str | None) -> None:
    selector.assert_valid_rd_fast_checks_mode(value)


@pytest.mark.parametrize("value", ["Hosted", "cloud", "true", "1"])
def test_assert_valid_rd_fast_checks_rejects_unknown(value: str) -> None:
    with pytest.raises(ValueError, match=selector.RD_FAST_CHECKS_VAR):
        selector.assert_valid_rd_fast_checks_mode(value)


def test_required_check_expression_includes_fast_checks_variable() -> None:
    expr = selector.required_check_runs_on_expression()
    assert selector.RD_FAST_CHECKS_VAR in expr
    assert "merge_group" in expr
    assert "ubuntu-latest" in expr
    assert "d-sorg-fleet" in expr


def test_default_expression_does_not_include_fast_checks_variable() -> None:
    expr = selector.default_runs_on_expression()
    assert selector.RD_FAST_CHECKS_VAR not in expr


def test_hosted_required_jobs_match_policy() -> None:
    jobs = selector.hosted_required_check_jobs(REPO_ROOT)
    expected = {
        ("ci-standard.yml", "quality-gate"),
        ("ci-standard.yml", "tests-required"),
        ("ci-standard.yml", "tests"),
        ("anti-phantom-merge.yml", "guard"),
        ("ci-secrets.yml", "detect-secrets"),
        ("frontend-tests.yml", "frontend-required"),
        ("frontend-tests.yml", "typecheck"),
    }
    assert expected <= jobs


def test_pick_runner_bash_fragment_validates_fast_checks_mode() -> None:
    function_body = selector.pick_runner_emit_fast_checks_runner_function_bash()
    assert "hosted" in function_body and "local" in function_body
    assert selector.RD_FAST_CHECKS_VAR in function_body
    assert selector.pick_runner_fast_checks_bash_fragment().strip() == "emit_fast_checks_runner"


def _ci_standard_pick_runner_select_script() -> str:
    import yaml

    path = REPO_ROOT / ".github/workflows/ci-standard.yml"
    steps = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]["pick-runner"]["steps"]
    select = next(step for step in steps if step.get("id") == "select")
    return str(select["run"])


def test_pick_runner_select_defines_canonical_emit_fast_checks_function() -> None:
    script = _ci_standard_pick_runner_select_script()
    canonical = selector.pick_runner_emit_fast_checks_runner_function_bash().strip()
    assert canonical in script, "pick-runner select must embed ci_runner_selector emit function"


def test_pick_runner_select_emits_fast_checks_before_every_success_exit() -> None:
    """Regression: early return paths must set fast_checks_runner (merge-queue gate)."""
    script = _ci_standard_pick_runner_select_script()
    lines = script.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped != "exit 0":
            continue
        prev = lines[index - 1].strip() if index > 0 else ""
        assert prev == "emit_fast_checks_runner", (
            f"line {index + 1}: exit 0 must immediately follow emit_fast_checks_runner; got {prev!r}"
        )


def test_workflows_use_canonical_required_check_expression() -> None:
    import yaml

    seen: set[tuple[str, str]] = set()
    cases = [
        (REPO_ROOT / ".github/workflows/anti-phantom-merge.yml", "guard", selector.required_check_runs_on_expression()),
        (REPO_ROOT / ".github/workflows/ci-secrets.yml", "detect-secrets", selector.required_check_runs_on_expression()),
        (REPO_ROOT / ".github/workflows/frontend-tests.yml", "typecheck", selector.frontend_required_check_runs_on_expression()),
        (REPO_ROOT / ".github/workflows/frontend-tests.yml", "frontend-required", selector.frontend_required_check_runs_on_expression()),
    ]
    for path, job_id, canonical in cases:
        key = (str(path), job_id)
        assert key not in seen, key
        seen.add(key)
        job = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"][job_id]
        assert str(job["runs-on"]).strip() == canonical.strip()


def test_runs_on_uses_fast_checks_lane_detects_markers() -> None:
    assert selector.runs_on_uses_fast_checks_lane(
        selector.required_check_runs_on_expression()
    )
    assert selector.runs_on_uses_fast_checks_lane(
        selector.pick_runner_fast_checks_runs_on_expression()
    )
    assert not selector.runs_on_uses_fast_checks_lane(selector.default_runs_on_expression())
