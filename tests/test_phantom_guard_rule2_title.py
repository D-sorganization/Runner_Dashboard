"""Regression tests for anti-phantom Rule 2 title matching (#4268 H-05)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.phantom_guard_title import title_triggers_rule2

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "phantom_guard_rule2_titles.json"
_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "anti-phantom-merge.yml"
_VERIFY_ACTION = Path(__file__).resolve().parents[1] / ".github" / "actions" / "verify-issue-resolution" / "action.yml"


def _fixture() -> dict[str, list[str]]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_ordinary_titles_do_not_trigger_rule2() -> None:
    for title in _fixture()["ordinary_pass"]:
        assert not title_triggers_rule2(title), title


def test_feature_claim_titles_still_trigger_rule2() -> None:
    for title in _fixture()["feature_claim_fail_when_no_code"]:
        assert title_triggers_rule2(title), title


def test_anti_phantom_workflow_uses_shared_title_helper() -> None:
    script = "\n".join(
        step.get("run", "") for step in yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["guard"]["steps"]
    )
    assert "phantom_guard_title.py" in script
    assert "^feat[(:]|Implement|System|Engine|Framework" not in script


def test_verify_issue_resolution_action_uses_shared_title_helper() -> None:
    text = _VERIFY_ACTION.read_text(encoding="utf-8")
    assert "phantom_guard_title.py" in text
    assert "^feat[(:]|Implement|System|Engine|Framework" not in text


def test_rule3_path_extractor_runs_after_guard_script_checkout() -> None:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["guard"]["steps"]
    run_steps = [s for s in steps if "run" in s]
    checkout_idx = next(i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/checkout"))
    guard_idx = next(i for i, s in enumerate(steps) if s.get("name") == "Anti-phantom guard")
    assert checkout_idx < guard_idx
    script = run_steps[0].get("run", "")
    assert "phantom_guard_issue_paths.py" in script
    assert "Rule 3 (issue path mismatch)" in script
