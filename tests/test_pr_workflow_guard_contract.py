"""Contract tests for the reusable PR freshness guard workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_GUARD = _WORKFLOWS / "reusable-pr-freshness-guard.yml"
_CALLERS = ["ci-standard.yml", "ci-secrets.yml", "docker-build.yml", "frontend-tests.yml", "ci-auto-deploy-regression.yml"]


def _triggers(data: dict) -> dict | str | list:
    return data.get(True) or data.get("on") or {}


def test_reusable_pr_guard_job_wiring_and_runner() -> None:
    data = yaml.safe_load(_GUARD.read_text(encoding="utf-8"))
    guard = data["jobs"]["guard"]
    steps = guard["steps"]
    assert steps[0]["uses"].startswith("actions/checkout@")
    assert steps[1]["uses"] == "./.github/actions/pr-early-exit"
    runs_on = str(guard["runs-on"])
    assert "d-sorg-fleet" in runs_on and "ubuntu-latest" not in runs_on


def test_reusable_pr_guard_concurrency_scoped_per_caller_workflow() -> None:
    data = yaml.safe_load(_GUARD.read_text(encoding="utf-8"))
    block = data.get("concurrency", {})
    cancel = str(block.get("cancel-in-progress", ""))
    assert "pull_request" in cancel
    assert cancel != "true"
    group = str(block.get("group", ""))
    assert "github.workflow" in group
    assert "github.event.pull_request.number" in group or "github.run_id" in group


def test_jobs_needing_pr_guard_use_always_when_workflow_has_merge_group() -> None:
    for name in _CALLERS:
        data = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
        triggers = _triggers(data)
        if "merge_group" not in triggers and "merge_group" not in str(triggers):
            continue
        for job_name, job in (data.get("jobs") or {}).items():
            needs = job.get("needs") or []
            if "pr-guard" not in needs and "pr-guard" not in str(needs):
                continue
            assert "always()" in str(job.get("if") or ""), f"{name}:{job_name}"


def test_pr_guard_job_is_pull_request_only_on_callers() -> None:
    for name in _CALLERS:
        job_if = str(yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]["pr-guard"].get("if") or "")
        assert "github.event_name == 'pull_request'" in job_if, name
        assert "head.repo.full_name == github.repository" in job_if, name


def _pr_guard_aggregator_if_allows_skipped(job_if: object) -> bool:
    text = str(job_if)
    return "!cancelled()" in text and "needs.pr-guard.result == 'skipped'" in text


def test_aggregators_tolerate_pr_guard_skipped_on_merge_group_and_push() -> None:
    ci = yaml.safe_load((_WORKFLOWS / "ci-standard.yml").read_text(encoding="utf-8"))
    for job_name in ("tests-required", "quality-gate"):
        job = ci["jobs"][job_name]
        assert "pr-guard" in str(job.get("needs"))
        assert _pr_guard_aggregator_if_allows_skipped(job.get("if")), job_name
    frontend = yaml.safe_load((_WORKFLOWS / "frontend-tests.yml").read_text(encoding="utf-8"))
    assert _pr_guard_aggregator_if_allows_skipped(frontend["jobs"]["frontend-required"]["if"])
