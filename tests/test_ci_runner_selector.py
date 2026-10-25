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
    fragment = selector.pick_runner_fast_checks_bash_fragment()
    assert "hosted" in fragment and "local" in fragment
    assert selector.RD_FAST_CHECKS_VAR in fragment


def test_workflows_use_canonical_required_check_expression() -> None:
    workflows = {
        REPO_ROOT / ".github/workflows/anti-phantom-merge.yml": "guard",
        REPO_ROOT / ".github/workflows/ci-secrets.yml": "detect-secrets",
        REPO_ROOT / ".github/workflows/frontend-tests.yml": "typecheck",
    }
    canonical = selector.required_check_runs_on_expression().strip()
    for path, job_id in workflows.items():
        import yaml

        job = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"][job_id]
        assert str(job["runs-on"]).strip() == canonical


def test_runs_on_uses_fast_checks_lane_detects_markers() -> None:
    assert selector.runs_on_uses_fast_checks_lane(
        selector.required_check_runs_on_expression()
    )
    assert selector.runs_on_uses_fast_checks_lane(
        selector.pick_runner_fast_checks_runs_on_expression()
    )
    assert not selector.runs_on_uses_fast_checks_lane(selector.default_runs_on_expression())
