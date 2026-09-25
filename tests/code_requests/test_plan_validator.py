"""CR-4 (#1285): the planner's output is validated, not trusted.

Fixture-driven contract for ``code_requests.plan_validator``: a good plan is accepted;
missing sections, missing or unknown tier labels, cyclic or dangling dependencies and
invalid turnover documents are rejected with actionable messages the planner can be
re-prompted with.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from code_requests.plan import PlanFormatError, compute_waves, parse_plan_output  # noqa: E402
from code_requests.plan_render import TurnoverIdentity, render_turnover  # noqa: E402
from code_requests.plan_validator import validate_plan, validate_plan_output  # noqa: E402


def child(key: str, deps: list[str] | None = None, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "key": key,
        "title": f"Implement {key}",
        "objective": f"Deliver {key}.",
        "execution_instructions": [f"Edit backend/{key}.py", f"Add tests/test_{key}.py"],
        "file_scope": {"allowed": [f"backend/{key}.py", f"tests/test_{key}.py"], "forbidden": ["frontend/"]},
        "acceptance_criteria": [f"{key} behaves as specified"],
        "validation_commands": [{"command": f"pytest tests/test_{key}.py", "expect": "all tests pass"}],
        "dependencies": deps or [],
        "out_of_scope": ["UI changes"],
        "tier": "tier:cli",
        "complexity": "small",
        "task_class": "feature",
        "key_decisions": ["Reuse the existing store"],
        "next_steps": [f"Write the failing test for {key}"],
    }
    item.update(overrides)
    return item


def good_plan() -> dict[str, Any]:
    return {
        "epic": {
            "title": "Epic: widget pipeline",
            "summary": "Build the widget pipeline in three slices.",
            "acceptance_criteria": ["All slices merged", "Pipeline runs end to end"],
        },
        "children": [child("a"), child("b", ["a"]), child("c", ["a", "#77"])],
        "duplicates": [{"number": 12, "title": "Old widget idea", "reason": "superseded, not a duplicate"}],
    }


def errors_for(plan: dict[str, Any]) -> list[str]:
    _, errors = validate_plan_output(json.dumps(plan), repository="Runner_Dashboard")
    return errors


def test_a_good_plan_is_accepted() -> None:
    plan, errors = validate_plan_output(json.dumps(good_plan()), repository="Runner_Dashboard")
    assert errors == []
    assert plan is not None
    assert [c.key for c in plan.children] == ["a", "b", "c"]


def test_the_plan_may_arrive_inside_a_fenced_block_with_prose_around_it() -> None:
    text = "Here is the plan.\n\n```json\n" + json.dumps(good_plan()) + "\n```\nDone."
    assert parse_plan_output(text).epic.title == "Epic: widget pipeline"


def test_output_without_json_is_a_format_error() -> None:
    with pytest.raises(PlanFormatError, match="JSON"):
        parse_plan_output("I could not produce a plan.")
    _, errors = validate_plan_output("no plan here", repository="Runner_Dashboard")
    assert errors and "JSON" in errors[0]


@pytest.mark.parametrize(
    "section",
    [
        "objective",
        "execution_instructions",
        "file_scope",
        "acceptance_criteria",
        "validation_commands",
        "out_of_scope",
        "next_steps",
    ],
)
def test_a_child_missing_a_required_section_is_rejected(section: str) -> None:
    plan = good_plan()
    del plan["children"][1][section]
    errors = errors_for(plan)
    assert any("b" in e and section in e for e in errors), errors


@pytest.mark.parametrize("section", ["execution_instructions", "acceptance_criteria", "validation_commands"])
def test_an_empty_required_list_is_rejected(section: str) -> None:
    plan = good_plan()
    plan["children"][0][section] = []
    assert any(section in e for e in errors_for(plan))


@pytest.mark.parametrize("tier", [None, "", "tier:gpu", "cli"])
def test_a_missing_or_unknown_tier_label_is_rejected(tier: str | None) -> None:
    plan = good_plan()
    if tier is None:
        del plan["children"][0]["tier"]
    else:
        plan["children"][0]["tier"] = tier
    assert any("tier" in e for e in errors_for(plan))


def test_unknown_complexity_and_task_class_are_rejected() -> None:
    plan = good_plan()
    plan["children"][0]["complexity"] = "huge"
    plan["children"][1]["task_class"] = "misc"
    errors = errors_for(plan)
    assert any("complexity" in e for e in errors)
    assert any("task_class" in e for e in errors)


def test_cyclic_dependencies_are_rejected() -> None:
    plan = good_plan()
    plan["children"][0]["dependencies"] = ["c"]
    errors = errors_for(plan)
    assert any("cycle" in e.lower() for e in errors), errors


def test_a_dependency_on_an_unknown_key_is_rejected() -> None:
    plan = good_plan()
    plan["children"][1]["dependencies"] = ["zzz"]
    assert any("zzz" in e for e in errors_for(plan))


def test_duplicate_child_keys_are_rejected() -> None:
    plan = good_plan()
    plan["children"][2]["key"] = "a"
    assert any("duplicate" in e.lower() for e in errors_for(plan))


def test_a_turnover_with_an_unedited_placeholder_is_rejected() -> None:
    plan = good_plan()
    plan["children"][0]["next_steps"] = ["Run <command> to start"]
    errors = errors_for(plan)
    assert any("placeholder" in e.lower() and "a" in e for e in errors), errors


def test_a_turnover_carrying_a_secret_is_rejected() -> None:
    plan = good_plan()
    plan["children"][0]["key_decisions"] = ["use token ghp_" + "A" * 36]
    assert any("secret" in e.lower() or "token" in e.lower() for e in errors_for(plan))


def test_waves_follow_the_dependency_order() -> None:
    plan = parse_plan_output(json.dumps(good_plan()))
    assert compute_waves(plan.children) == [["a"], ["b", "c"]]


def test_the_rendered_turnover_passes_the_fleet_handoff_rules() -> None:
    plan = parse_plan_output(json.dumps(good_plan()))
    identity = TurnoverIdentity(
        repository="D-sorganization/Runner_Dashboard",
        base_branch="main",
        baseline_sha="95ba642",
        governing_issue="#1300",
    )
    doc = render_turnover(plan.children[0], identity)
    assert doc.startswith("<!-- turnover:v1 -->")
    for heading in ("## Identity", "## Objective and status", "## Next steps", "## Change log"):
        assert heading in doc
    assert "Baseline commit: 95ba642" in doc
    assert "Governing issue/epic: #1300" in doc
    assert validate_plan(plan, repository="Runner_Dashboard") == []


def test_validation_does_not_mutate_the_submission() -> None:
    raw = good_plan()
    snapshot = copy.deepcopy(raw)
    errors_for(raw)
    assert raw == snapshot
