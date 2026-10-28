"""Contract tests for CI workflow concurrency (issues #2764, #2768)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shared_scripts.ci_workflow_concurrency import (
    CANCEL_IN_PROGRESS_EXPR,
    CI_STANDARD_CANCEL_IN_PROGRESS_EXPR,
    CI_STANDARD_WORKFLOW_CONCURRENCY_GROUP_EXPR,
    WORKFLOW_CONCURRENCY_GROUP_EXPR,
    pr_number_from_queue_ref,
)

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

_MERGE_GROUP_HEAVY = [
    "docker-build.yml",
    "frontend-tests.yml",
    "ci-secrets.yml",
    "ci-auto-deploy-regression.yml",
]

_CI_STANDARD = "ci-standard.yml"

_PR_ONLY_CANCEL = "${{ github.event_name == 'pull_request' }}"


def _load(name: str) -> dict:
    data = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("gh-readonly-queue/main/pr-77-abcdef0123456789abcdef0123456789", 77),  # pragma: allowlist secret
        ("refs/heads/gh-readonly-queue/release/2.0/pr-5-abc1234", 5),
        ("feat/pr-77-abc", None),
        ("gh-readonly-queue/main/pr-x-abc", None),
        ("", None),
    ],
)
def test_pr_number_from_queue_ref(ref: str, expected: int | None) -> None:
    assert pr_number_from_queue_ref(ref) == expected


@pytest.mark.parametrize("name", _MERGE_GROUP_HEAVY)
def test_heavy_workflows_use_canonical_concurrency_block(name: str) -> None:
    block = _load(name)["concurrency"]
    assert block["group"] == WORKFLOW_CONCURRENCY_GROUP_EXPR
    assert block["cancel-in-progress"] == CANCEL_IN_PROGRESS_EXPR


@pytest.mark.parametrize("name", _MERGE_GROUP_HEAVY)
def test_merge_group_never_uses_cancel_in_progress(name: str) -> None:
    assert _load(name)["concurrency"]["cancel-in-progress"] == _PR_ONLY_CANCEL


@pytest.mark.parametrize("name", _MERGE_GROUP_HEAVY)
def test_merge_group_concurrency_is_unique_per_queue_entry(name: str) -> None:
    group = _load(name)["concurrency"]["group"]
    assert "merge_group.head_ref" in group
    assert "merge_group-pr-" not in group
    assert "split(" not in group


@pytest.mark.parametrize("name", _MERGE_GROUP_HEAVY)
def test_main_push_still_unique_per_sha(name: str) -> None:
    group = _load(name)["concurrency"]["group"]
    assert "github.sha" in group


def test_ci_standard_uses_main_push_shared_concurrency_group() -> None:
    block = _load(_CI_STANDARD)["concurrency"]
    assert block["group"] == CI_STANDARD_WORKFLOW_CONCURRENCY_GROUP_EXPR
    assert block["cancel-in-progress"] == CI_STANDARD_CANCEL_IN_PROGRESS_EXPR
    assert "refs/heads/main' && 'main'" in block["group"]


def test_ci_standard_merge_group_still_not_cancel_in_progress() -> None:
    cancel = _load(_CI_STANDARD)["concurrency"]["cancel-in-progress"]
    assert "merge_group" not in cancel
    assert cancel == CI_STANDARD_CANCEL_IN_PROGRESS_EXPR


def test_cancel_in_progress_limited_to_pull_request() -> None:
    assert CANCEL_IN_PROGRESS_EXPR == _PR_ONLY_CANCEL
    assert "merge_group" not in CANCEL_IN_PROGRESS_EXPR


# --- #3046 regression lock: merge_group/push never cancel-in-progress ------
#
# Reserved-runner routing (#3046) only helps a merge_group job if it is never
# cancelled mid-run by a later push or queue entry. Not a behavior change —
# the expression below already evaluates to False for both events on main;
# this pins that fact so a future edit to the concurrency block can't widen
# cancellation to merge_group or push without failing a test.


def _cancel_in_progress_for_event(expr: str, event_name: str) -> bool:
    """Evaluate the fixed-shape ``${{ github.event_name == '<literal>' }}`` expression."""
    body = expr.strip()
    assert body.startswith("${{") and body.endswith("}}"), expr
    lhs, op, rhs = body[3:-2].split()
    assert (lhs, op) == ("github.event_name", "=="), expr
    return event_name == rhs.strip("'")


@pytest.mark.parametrize("name", ["ci-standard.yml", "frontend-tests.yml", "ci-secrets.yml"])
@pytest.mark.parametrize("event_name", ["merge_group", "push"])
def test_merge_group_and_push_never_cancel_in_progress(name: str, event_name: str) -> None:
    expr = _load(name)["concurrency"]["cancel-in-progress"]
    assert _cancel_in_progress_for_event(expr, event_name) is False
