"""Tests for issue #3045: a browser-runner-label switch on frontend-tests.yml.

Verified red on origin/main @ 91057286 before the runs-on edit landed, green
after. See scripts/fork_pr_runner_guard.py for the fork-PR-routing contract
these jobs must keep (RM#1989).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.fork_pr_guard_analysis import normalize, split_top_level
from scripts.fork_pr_runner_guard import find_violations, fork_routed

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "frontend-tests.yml"
WORKFLOW_DIR = WORKFLOW_PATH.parent

# The exact pre-switch runs-on expressions (origin/main @ 91057286), one shape
# per job family. Diffing against these literally proves the only change is
# the inserted `|| vars.CI_BROWSER_RUNNER_LABEL` alternative.
_LONG_CHAIN_OLD = (
    "${{ github.event.pull_request && github.event.pull_request.head.repo.full_name "
    "!= github.repository && 'ubuntu-latest' || !github.event.repository.private && "
    "vars.CI_RUNNER_MODE != 'local' && 'ubuntu-latest' || github.event_name == "
    "'merge_group' && vars.CI_QUEUE_RUNNER_MODE == 'hosted' && 'ubuntu-latest' || "
    "github.event_name == 'pull_request' && vars.CI_PR_RUNNER_MODE == 'hosted' && "
    "'ubuntu-latest' || 'd-sorg-fleet' }}"
)
_SHORT_CHAIN_OLD = (
    "${{ github.event.pull_request && github.event.pull_request.head.repo.full_name "
    "!= github.repository && 'ubuntu-latest' || !github.event.repository.private && "
    "vars.CI_RUNNER_MODE != 'local' && 'ubuntu-latest' || 'd-sorg-fleet' }}"
)
_OLD_RUNS_ON: dict[str, str] = {
    "frontend-required": _LONG_CHAIN_OLD,
    "frontend-scope": _LONG_CHAIN_OLD,
    "typecheck": _LONG_CHAIN_OLD,
    "vitest": _LONG_CHAIN_OLD,
    "perf-budget": _LONG_CHAIN_OLD,
    "playwright-e2e": _SHORT_CHAIN_OLD,
    "staff-console-e2e": _SHORT_CHAIN_OLD,
}
_SWITCH_SUFFIX = "|| vars.CI_BROWSER_RUNNER_LABEL || 'd-sorg-fleet' }}"


def _load_jobs() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return data["jobs"]


def test_every_frontend_job_uses_browser_label_switch() -> None:
    jobs = _load_jobs()
    assert set(jobs) == set(_OLD_RUNS_ON), "job list changed; update _OLD_RUNS_ON"
    for job_id, job in jobs.items():
        runs_on = job["runs-on"]
        assert isinstance(runs_on, str), f"{job_id}: runs-on must stay a single expression"
        assert runs_on.endswith(_SWITCH_SUFFIX), (
            f"{job_id}: runs-on must fall back through "
            f"vars.CI_BROWSER_RUNNER_LABEL before 'd-sorg-fleet'"
        )


def test_fork_and_hosted_branches_unchanged() -> None:
    jobs = _load_jobs()
    for job_id, job in jobs.items():
        old = _OLD_RUNS_ON[job_id]
        expected = old.replace("|| 'd-sorg-fleet' }}", _SWITCH_SUFFIX)
        assert job["runs-on"] == expected, (
            f"{job_id}: runs-on changed somewhere other than the trailing "
            f"'d-sorg-fleet' fallback"
        )
        # Fork PRs must still be routed to a hosted label first (RM#1989);
        # appending an alternative at the tail must not disturb that.
        assert fork_routed(job), f"{job_id}: no longer routes fork PRs to a hosted label"

    violations = [v for v in find_violations(WORKFLOW_DIR) if v.startswith("frontend-tests.yml::")]
    assert not violations, violations


# A minimal, read-only evaluator for this workflow's specific runs-on grammar:
# `&&`-joined conjuncts, alternatives joined by top-level `||`, GitHub's
# short-circuit semantics (an operator returns the deciding operand's value,
# not a bare bool). Scoped to the handful of atoms this file actually uses.
def _eval_token(token: str, context: dict[str, object]) -> object:
    token = token.strip()
    if token.startswith("!"):
        return not _truthy(_eval_token(token[1:], context))
    for op in ("!=", "=="):
        if op in token:
            left, right = (part.strip() for part in token.split(op, 1))
            equal = _eval_token(left, context) == _eval_token(right, context)
            return equal if op == "==" else not equal
    if token.startswith("'") and token.endswith("'"):
        return token[1:-1]
    return context.get(token)


def _truthy(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    return bool(value)


def _eval_runs_on(expr: str, context: dict[str, object]) -> object:
    last_value: object = None
    for alternative in split_top_level(normalize(expr), "||"):
        value: object = True
        for conjunct in split_top_level(alternative, "&&"):
            value = _eval_token(conjunct, context)
            if not _truthy(value):
                break
        last_value = value
        if _truthy(value):
            return value
    return last_value


# Scenarios that reach the self-hosted tail today (none of the hosted-escape
# alternatives fire): a same-repo PR, a plain main push, and a merge_group run.
_SELF_HOSTED_SCENARIOS: dict[str, dict[str, object]] = {
    "same-repo pull_request": {
        "github.event.pull_request": {"head": {"repo": {"full_name": "D-sorganization/Runner_Dashboard"}}},
        "github.event.pull_request.head.repo.full_name": "D-sorganization/Runner_Dashboard",
        "github.repository": "D-sorganization/Runner_Dashboard",
        "github.event.repository.private": True,
        "github.event_name": "pull_request",
        "vars.CI_RUNNER_MODE": "local",
        "vars.CI_QUEUE_RUNNER_MODE": None,
        "vars.CI_PR_RUNNER_MODE": None,
    },
    "main push": {
        "github.event.pull_request": None,
        "github.event.pull_request.head.repo.full_name": None,
        "github.repository": "D-sorganization/Runner_Dashboard",
        "github.event.repository.private": True,
        "github.event_name": "push",
        "vars.CI_RUNNER_MODE": "local",
        "vars.CI_QUEUE_RUNNER_MODE": None,
        "vars.CI_PR_RUNNER_MODE": None,
    },
    "merge_group": {
        "github.event.pull_request": None,
        "github.event.pull_request.head.repo.full_name": None,
        "github.repository": "D-sorganization/Runner_Dashboard",
        "github.event.repository.private": True,
        "github.event_name": "merge_group",
        "vars.CI_RUNNER_MODE": "local",
        "vars.CI_QUEUE_RUNNER_MODE": None,
        "vars.CI_PR_RUNNER_MODE": None,
    },
}


def test_no_frontend_job_targets_bare_fleet_when_switch_set() -> None:
    jobs = _load_jobs()
    for scenario_name, base_context in _SELF_HOSTED_SCENARIOS.items():
        context = {**base_context, "vars.CI_BROWSER_RUNNER_LABEL": "d-sorg-fleet-browser"}
        for job_id, job in jobs.items():
            resolved = _eval_runs_on(job["runs-on"], context)
            assert resolved == "d-sorg-fleet-browser", (
                f"{job_id} resolved to {resolved!r} under {scenario_name!r} with the "
                f"switch set; it must never fall through to bare 'd-sorg-fleet'"
            )

        # Same scenario, switch unset: must resolve exactly like today's main.
        unset_context = {**base_context, "vars.CI_BROWSER_RUNNER_LABEL": None}
        for job_id, job in jobs.items():
            resolved = _eval_runs_on(job["runs-on"], unset_context)
            old_resolved = _eval_runs_on(_OLD_RUNS_ON[job_id], unset_context)
            assert resolved == old_resolved == "d-sorg-fleet", (
                f"{job_id} under {scenario_name!r} with the switch unset must still "
                f"resolve to bare 'd-sorg-fleet', same as main"
            )
