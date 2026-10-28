from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOWS_DIR = Path(__file__).parent.parent / ".github" / "workflows"
_REAPER_WF_PATH = _WORKFLOWS_DIR / "util-queued-job-reaper.yml"


def test_reaper_workflow_exists() -> None:
    assert _REAPER_WF_PATH.exists()


def test_reaper_workflow_concurrency() -> None:
    data = yaml.safe_load(_REAPER_WF_PATH.read_text(encoding="utf-8"))
    assert "concurrency" in data
    block = data["concurrency"]
    assert block.get("group") == "queued-job-reaper"
    assert block.get("cancel-in-progress") is True


def test_reaper_workflow_inputs() -> None:
    data = yaml.safe_load(_REAPER_WF_PATH.read_text(encoding="utf-8"))
    triggers = data.get("on") or data.get(True) or {}
    assert "workflow_dispatch" in triggers
    dispatch = triggers["workflow_dispatch"] or {}
    inputs = dispatch.get("inputs", {})

    assert "dry-run" in inputs
    assert "min-age-minutes" in inputs
    assert "max-cancel" in inputs

    assert inputs["dry-run"].get("default") == "true"
    assert inputs["min-age-minutes"].get("default") == "20"
    assert inputs["max-cancel"].get("default") == "10"


def test_reaper_workflow_uses_github_token_not_jules_app() -> None:
    text = _REAPER_WF_PATH.read_text(encoding="utf-8")
    assert "JULES_APP" not in text
    assert "reap_github_workflow_runs.py" in text
    data = yaml.safe_load(text)
    perms = data.get("permissions") or {}
    assert perms.get("actions") == "write"
    assert perms.get("pull-requests") == "read"


def test_reaper_workflow_kill_switch() -> None:
    content = _REAPER_WF_PATH.read_text(encoding="utf-8")
    assert "QUEUED_JOB_REAPER_DISABLED" in content
    # Should check kill switch in the workflow (e.g. env or run command)
    data = yaml.safe_load(content)
    jobs = data.get("jobs", {})
    assert "reap" in jobs
    steps = jobs["reap"].get("steps", [])

    # Verify there is a step referencing QUEUED_JOB_REAPER_DISABLED
    found_kill_switch_check = False
    for step in steps:
        env_block = step.get("env", {})
        if "QUEUED_JOB_REAPER_DISABLED" in str(env_block) or "DISABLED_FLAG" in str(env_block):
            found_kill_switch_check = True
            break
    assert found_kill_switch_check, "Could not find step that references the QUEUED_JOB_REAPER_DISABLED variable"
