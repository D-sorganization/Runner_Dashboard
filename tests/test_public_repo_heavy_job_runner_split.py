"""Heavy jobs never flip to hosted once the repo goes public (#3084, epic #3077 slice 6).

Playwright e2e, the Staff Console e2e suite and the auto-deploy regression
job already route fork PRs to a hosted runner (RM#1989) and keep the same
required-check names as before this issue (AC #2); the one real gap is a
second, unrelated runs-on branch -- ``!github.event.repository.private &&
vars.CI_RUNNER_MODE != 'local' && 'ubuntu-latest'`` -- that silently sends
these jobs to a hosted runner the moment the repository visibility flips to
public, which docker-build.yml's own `docker-image` job deliberately keeps
(#2577: the self-hosted docker pool starves the deploy check once public).
These three jobs have no such starvation concern and must stay self-hosted.

config/public_repo_heavy_jobs.json is the single list (DRY) naming which
jobs this applies to and the shared environment/group-label names; nothing
else in this repo hard-codes those two strings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from scripts.fork_pr_runner_guard import fork_routed, requires_conjunct, same_repo_conditioned

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
POLICY = json.loads((REPO_ROOT / "config" / "public_repo_heavy_jobs.json").read_text(encoding="utf-8"))
PUBLIC_HOSTED_BRANCH = "!github.event.repository.private && vars.CI_RUNNER_MODE != 'local' && 'ubuntu-latest'"


def _job(entry: dict[str, str]) -> dict[str, Any]:
    jobs = yaml.safe_load((WORKFLOWS / entry["workflow"]).read_text(encoding="utf-8"))["jobs"]
    return jobs[entry["job_id"]]


def test_policy_lists_at_least_one_job() -> None:
    assert POLICY["jobs"]


def test_heavy_jobs_never_route_to_hosted_when_public() -> None:
    for entry in POLICY["jobs"]:
        runs_on = str(_job(entry).get("runs-on", ""))
        assert PUBLIC_HOSTED_BRANCH not in runs_on, f"{entry['workflow']}::{entry['job_id']} still has the branch"


def test_heavy_jobs_end_on_the_shared_group_label() -> None:
    for entry in POLICY["jobs"]:
        runs_on = " ".join(str(_job(entry).get("runs-on", "")).split())
        assert runs_on.endswith(f"'{POLICY['group_label']}' }}}}"), f"{entry['workflow']}::{entry['job_id']}"


def test_heavy_jobs_carry_the_shared_environment() -> None:
    for entry in POLICY["jobs"]:
        job = _job(entry)
        assert job.get("environment", {}).get("name") == POLICY["environment"], (
            f"{entry['workflow']}::{entry['job_id']} missing environment: name: {POLICY['environment']}"
        )


def test_heavy_jobs_still_guard_fork_prs() -> None:
    """The #3084 edit must not touch the existing fork-PR guard (RM#1989)."""
    for entry in POLICY["jobs"]:
        job = _job(entry)
        assert (
            fork_routed(job) or requires_conjunct(job.get("if"), guard_text()) or same_repo_conditioned(job.get("if"))
        ), f"{entry['workflow']}::{entry['job_id']} lost its fork-PR guard"


def guard_text() -> str:
    return "!github.event.pull_request || github.event.pull_request.head.repo.full_name == github.repository"


def test_required_check_names_unchanged() -> None:
    expected = {
        ("frontend-tests.yml", "playwright-e2e"): "Playwright E2E smoke tests",
        ("frontend-tests.yml", "staff-console-e2e"): "Staff Console e2e (fake providers)",
        ("ci-auto-deploy-regression.yml", "auto-deploy-regression"): "Auto-deploy live regression",
    }
    for entry in POLICY["jobs"]:
        key = (entry["workflow"], entry["job_id"])
        assert _job(entry)["name"] == expected[key], key


def test_docs_mention_the_shared_names() -> None:
    text = (REPO_ROOT / "docs" / "runner-labels.md").read_text(encoding="utf-8")
    assert POLICY["group_label"] in text
    assert POLICY["environment"] in text
