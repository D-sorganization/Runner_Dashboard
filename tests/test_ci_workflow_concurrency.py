"""Contract tests for CI workflow concurrency (issues #2764, #2768)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shared_scripts.ci_workflow_concurrency import (
    CANCEL_IN_PROGRESS_EXPR,
    WORKFLOW_CONCURRENCY_GROUP_EXPR,
    merge_group_concurrency_token,
    pr_number_from_queue_ref,
)

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

_MERGE_GROUP_HEAVY = [
    "ci-standard.yml",
    "docker-build.yml",
    "frontend-tests.yml",
    "ci-secrets.yml",
    "ci-auto-deploy-regression.yml",
]

def _load(name: str) -> dict:
    data = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("gh-readonly-queue/main/pr-77-abcdef0123456789abcdef0123456789", 77),
        ("refs/heads/gh-readonly-queue/release/2.0/pr-5-abc1234", 5),
        ("feat/pr-77-abc", None),
        ("gh-readonly-queue/main/pr-x-abc", None),
        ("", None),
    ],
)
def test_pr_number_from_queue_ref(ref: str, expected: int | None) -> None:
    assert pr_number_from_queue_ref(ref) == expected


@pytest.mark.parametrize(
    ("ref", "expected_token"),
    [
        ("gh-readonly-queue/main/pr-77-abc1234567890", "merge_group-pr-77"),
        ("refs/heads/gh-readonly-queue/main/pr-12-deadbeef", "merge_group-pr-12"),
        ("not-a-queue-ref", None),
    ],
)
def test_merge_group_concurrency_token(ref: str, expected_token: str | None) -> None:
    assert merge_group_concurrency_token(ref) == expected_token


def test_merge_group_tokens_never_collide_across_prs() -> None:
    refs = [
        "gh-readonly-queue/main/pr-1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "gh-readonly-queue/main/pr-2-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]
    tokens = [merge_group_concurrency_token(r) for r in refs]
    assert len(set(tokens)) == len(tokens)


@pytest.mark.parametrize("name", _MERGE_GROUP_HEAVY)
def test_heavy_workflows_use_canonical_concurrency_block(name: str) -> None:
    block = _load(name)["concurrency"]
    assert block["group"] == WORKFLOW_CONCURRENCY_GROUP_EXPR
    assert block["cancel-in-progress"] == CANCEL_IN_PROGRESS_EXPR


@pytest.mark.parametrize("name", _MERGE_GROUP_HEAVY)
def test_main_push_still_unique_per_sha(name: str) -> None:
    group = _load(name)["concurrency"]["group"]
    assert "github.sha" in group
    assert "merge_group-pr-" in group


def test_cancel_in_progress_true_for_merge_group_not_push() -> None:
    assert "merge_group" in CANCEL_IN_PROGRESS_EXPR
    assert CANCEL_IN_PROGRESS_EXPR != "${{ github.event_name == 'pull_request' }}"


def test_reusable_pr_guard_checks_out_before_local_action() -> None:
    data = yaml.safe_load(
        (_WORKFLOWS / "reusable-pr-freshness-guard.yml").read_text(encoding="utf-8")
    )
    steps = data["jobs"]["guard"]["steps"]
    assert steps[0]["uses"].startswith("actions/checkout@")
    assert steps[1]["uses"] == "./.github/actions/pr-early-exit"


def test_workflow_group_expr_mentions_merge_group_head_ref() -> None:
    assert "merge_group.head_ref" in WORKFLOW_CONCURRENCY_GROUP_EXPR
    assert "split(" in WORKFLOW_CONCURRENCY_GROUP_EXPR


def _ci_step_run(job_id: str, step_name: str) -> str:
    data = yaml.safe_load((_WORKFLOWS / "ci-standard.yml").read_text(encoding="utf-8"))
    for step in data["jobs"][job_id]["steps"]:
        if step.get("name") == step_name:
            run = step.get("run")
            assert isinstance(run, str)
            return run
    raise AssertionError(f"missing step {step_name} in {job_id}")


def test_quality_gate_treats_cancelled_dependencies_as_neutral() -> None:
    run = _ci_step_run("quality-gate", "Require every gated job to succeed")
    assert "cancelled" in run and "superseded" in run.lower()


def test_tests_required_treats_cancelled_ci_health_as_neutral() -> None:
    run = _ci_step_run("tests-required", "Confirm Python test matrix")
    assert "cancelled" in run and "ci-health-check" in run
