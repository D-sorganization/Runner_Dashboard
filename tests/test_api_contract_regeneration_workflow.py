"""Contract tests for the API contract regeneration required check workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/api-contract-regeneration.yml"


def _load() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    if True in data and "on" not in data:
        data["on"] = data[True]
    return data


def test_workflow_exists_and_reports_required_context_name() -> None:
    assert WORKFLOW.is_file()
    jobs = _load()["jobs"]
    assert "api-contract-regeneration" in jobs
    assert jobs["api-contract-regeneration"]["name"] == "api-contract-regeneration"


def test_workflow_runs_on_merge_queue_and_verifies_with_check_flag() -> None:
    triggers = _load()["on"]
    assert "merge_group" in triggers
    assert "pull_request" in triggers
    run = WORKFLOW.read_text(encoding="utf-8")
    assert "gen-api-client.sh --check" in run
