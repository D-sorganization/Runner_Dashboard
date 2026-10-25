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


def test_reusable_pr_guard_has_concurrency() -> None:
    data = yaml.safe_load(_GUARD.read_text(encoding="utf-8"))
    assert data.get("concurrency", {}).get("cancel-in-progress") is True


def test_pr_guard_job_is_pull_request_only_on_callers() -> None:
    for name in _CALLERS:
        data = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
        pr_guard = data["jobs"]["pr-guard"]
        assert pr_guard.get("if") == "github.event_name == 'pull_request'", name
