"""Contract tests for the reusable PR freshness guard workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_GUARD = _WORKFLOWS / "reusable-pr-freshness-guard.yml"
_CALLERS = [
    "ci-standard.yml",
    "ci-secrets.yml",
    "docker-build.yml",
    "frontend-tests.yml",
    "ci-auto-deploy-regression.yml",
]


def test_reusable_pr_guard_checks_out_before_local_action() -> None:
    data = yaml.safe_load(_GUARD.read_text(encoding="utf-8"))
    steps = data["jobs"]["guard"]["steps"]
    assert steps[0]["uses"].startswith("actions/checkout@")
    assert steps[1]["uses"] == "./.github/actions/pr-early-exit"


def test_reusable_pr_guard_runs_on_fleet_not_hosted() -> None:
    data = yaml.safe_load(_GUARD.read_text(encoding="utf-8"))
    runs_on = data["jobs"]["guard"]["runs-on"]
    assert "d-sorg-fleet" in str(runs_on)
    assert "ubuntu-latest" not in str(runs_on)


def test_reusable_pr_guard_concurrency_scoped_per_caller_workflow() -> None:
    data = yaml.safe_load(_GUARD.read_text(encoding="utf-8"))
    block = data.get("concurrency", {})
    assert block.get("cancel-in-progress") is True
    group = str(block.get("group", ""))
    assert "github.workflow" in group
    assert "github.event.pull_request.number" in group or "github.run_id" in group


def test_jobs_needing_pr_guard_use_always_when_workflow_has_merge_group() -> None:
    """Downstream jobs may run on merge_group while pr-guard is skipped (needs + if: always())."""
    merge_group_workflows = (
        "ci-secrets.yml",
        "ci-auto-deploy-regression.yml",
        "docker-build.yml",
        "frontend-tests.yml",
    )
    for name in merge_group_workflows:
        data = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
        triggers = data.get("on") or {}
        if "merge_group" not in triggers and "merge_group" not in str(triggers):
            continue
        for job_name, job in data.get("jobs", {}).items():
            needs = job.get("needs") or []
            if "pr-guard" not in needs and "pr-guard" not in str(needs):
                continue
            job_if = str(job.get("if") or "")
            assert "always()" in job_if, f"{name}:{job_name} needs pr-guard but lacks always()"


def test_pr_guard_job_is_pull_request_only_on_callers() -> None:
    for name in _CALLERS:
        data = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
        pr_guard = data["jobs"]["pr-guard"]
        job_if = str(pr_guard.get("if") or "")
        assert "github.event_name == 'pull_request'" in job_if, name
        assert "head.repo.full_name == github.repository" in job_if, name


def _pr_guard_aggregator_if_allows_skipped(job_if: object) -> bool:
    text = str(job_if)
    return "!cancelled()" in text and "needs.pr-guard.result == 'skipped'" in text


def test_aggregators_tolerate_pr_guard_skipped_on_merge_group_and_push() -> None:
    """pr-guard is PR-only; merge_group/push must not fail aggregators when it is skipped."""
    ci = yaml.safe_load((_WORKFLOWS / "ci-standard.yml").read_text(encoding="utf-8"))
    for job_name in ("tests-required", "quality-gate"):
        job = ci["jobs"][job_name]
        assert "pr-guard" in job.get("needs", []) or "pr-guard" in str(job.get("needs"))
        assert _pr_guard_aggregator_if_allows_skipped(job.get("if")), job_name

    frontend = yaml.safe_load((_WORKFLOWS / "frontend-tests.yml").read_text(encoding="utf-8"))
    assert _pr_guard_aggregator_if_allows_skipped(frontend["jobs"]["frontend-required"]["if"])


def test_merge_group_event_skips_pr_guard_job() -> None:
    ci = yaml.safe_load((_WORKFLOWS / "ci-standard.yml").read_text(encoding="utf-8"))
    assert ci["on"].get("merge_group") is not None or "merge_group" in ci.get("on", {})
    job_if = str(ci["jobs"]["pr-guard"]["if"])
    assert "github.event_name == 'pull_request'" in job_if
