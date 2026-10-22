"""Reusable metadata jobs retain the privileged-event fork boundary."""

import pytest

from scripts import fork_pr_runner_guard as guard

pytestmark = pytest.mark.unit

TARGET = "github.event_name == 'pull_request_target'"


def test_target_only_callee_accepts_trusted_base_metadata() -> None:
    job = {
        "if": TARGET,
        "runs-on": "d-sorg-fleet",
        "steps": [{"uses": "actions/checkout@v4", "with": {"ref": "${{ github.sha }}"}}],
    }
    assert guard.job_violations("callee.yml", {"workflow_call"}, "counter", job) == []


@pytest.mark.parametrize("condition", [TARGET, f"{TARGET} && success()"])
@pytest.mark.parametrize("head", ["${{ github.event.pull_request.head.sha }}", "${{ github.head_ref }}"])
def test_target_only_callee_still_rejects_head_checkout(condition: str, head: str) -> None:
    job = {
        "if": condition,
        "runs-on": "d-sorg-fleet",
        "steps": [{"uses": "actions/checkout@v4", "with": {"ref": head}}],
    }
    violations = guard.job_violations("callee.yml", {"workflow_call"}, "counter", job)
    assert len(violations) == 1
    assert "checks out PR head" in violations[0]


def test_target_only_nested_callee_still_rejects_head_input() -> None:
    job = {
        "if": TARGET,
        "uses": "./.github/workflows/another.yml",
        "with": {"ref": "${{ github.event.pull_request.head.sha }}"},
    }
    violations = guard.job_violations("callee.yml", {"workflow_call"}, "nested", job)
    assert len(violations) == 1
    assert "passes a PR head ref" in violations[0]


@pytest.mark.parametrize(
    "condition",
    [
        f"{TARGET} || success()",
        f"success() || ({TARGET})",
        "github.event_name != 'pull_request'",
        "github.event_name == 'workflow_run'",
        "needs.counter.outputs.status == 'WARN'",
        f"({TARGET} && success()) || failure()",
    ],
)
def test_unknown_or_bypassable_callee_still_fails_closed(condition: str) -> None:
    job = {"if": condition, "runs-on": "d-sorg-fleet"}
    assert guard.job_violations("callee.yml", {"workflow_call"}, "counter", job)


@pytest.mark.parametrize("event", ["pull_request", "pull_request_review", "pull_request_review_comment"])
def test_target_callee_condition_does_not_exempt_direct_pr_triggers(event: str) -> None:
    job = {"if": TARGET, "runs-on": "d-sorg-fleet"}
    assert guard.job_violations("callee.yml", {"workflow_call", event}, "counter", job)


def test_target_callee_still_rejects_aliased_head_code() -> None:
    job = {
        "if": TARGET,
        "runs-on": "d-sorg-fleet",
        "env": {"PR_SHA": "${{ github.event.pull_request.head['sha'] }}"},
        "steps": [{"run": 'git checkout "$PR_SHA"'}],
    }
    violations = guard.job_violations("callee.yml", {"workflow_call"}, "counter", job)
    assert len(violations) == 1
    assert "checks out PR head" in violations[0]
