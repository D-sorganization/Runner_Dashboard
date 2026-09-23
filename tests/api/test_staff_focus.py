"""Fleet focus paragraph in staff prompts (issue #1239)."""

from __future__ import annotations

from typing import Any

import pytest
from staff import focus as focus_mod
from staff import runner as runner_mod
from staff.roles import RoleSpec

BOARD = {"kind": "board", "rank": 1, "item": "Ship validation suite", "project": "Gasification_Model",
         "tracking": "#5059", "acceptance": "CI green on main"}  # fmt: skip
OTHER = {"kind": "board", "rank": 2, "item": "Speed up putting", "project": "UpstreamDrift"}
ALL = {"kind": "directive", "text": "No new features tonight; fix red CI", "repo": "*", "priority": 1}
MINE = {"kind": "directive", "text": "Finish the charter", "repo": "Gasification_Model", "priority": 2}
THEIRS = {"kind": "directive", "text": "Tools only", "repo": "Tools", "priority": 1}


def _role() -> RoleSpec:
    return RoleSpec(
        name="issue-remediator", title="Issue Remediator", instructions="Fix issues.", providers=("claude",)
    )


@pytest.mark.unit
def test_paragraph_keeps_only_items_for_the_repo() -> None:
    text = focus_mod.focus_paragraph("Gasification_Model", [BOARD, OTHER, ALL, MINE, THEIRS])
    assert "Ship validation suite" in text and "(tracking #5059)" in text and "done when: CI green" in text
    assert "fix red CI" in text and "Finish the charter" in text
    assert "putting" not in text and "Tools only" not in text


@pytest.mark.unit
def test_paragraph_is_empty_when_nothing_applies() -> None:
    assert focus_mod.focus_paragraph("Tools_Private", [BOARD, OTHER, THEIRS]) == ""
    assert focus_mod.focus_paragraph("", [BOARD]) == ""


@pytest.mark.unit
def test_paragraph_is_capped() -> None:
    many = [{**ALL, "text": f"d{i}"} for i in range(12)]
    assert focus_mod.focus_paragraph("Tools", many).count("\n- [") == focus_mod.MAX_ITEMS


@pytest.mark.unit
def test_loader_failure_yields_no_items() -> None:
    def boom() -> list[dict[str, Any]]:
        raise RuntimeError("no RM checkout")

    assert focus_mod.load_items(boom) == []


@pytest.mark.unit
def test_plan_prompt_carries_focus_before_fleet_rules(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    role = RoleSpec(
        name="issue-remediator", title="Issue Remediator", instructions="Fix issues.", providers=("claude",)
    )
    staff = runner_mod.StaffRunner(roles_loader=lambda: {role.name: role}, focus_loader=lambda: [BOARD, ALL, THEIRS])
    plan = staff.plan(runner_mod.RunRequest(role="issue-remediator", repo="Gasification_Model", prompt="sweep"))
    assert plan.focus and plan.to_dict()["focus"] == plan.focus
    assert plan.prompt.index("Fleet focus") < plan.prompt.index("Fleet rules:")
    assert "Tools only" not in plan.prompt


@pytest.mark.unit
def test_plan_without_priorities_has_no_focus(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    role = RoleSpec(
        name="issue-remediator", title="Issue Remediator", instructions="Fix issues.", providers=("claude",)
    )
    staff = runner_mod.StaffRunner(roles_loader=lambda: {role.name: role}, focus_loader=lambda: [])
    plan = staff.plan(runner_mod.RunRequest(role="issue-remediator", repo="Tools", prompt="sweep"))
    assert plan.focus == "" and "Fleet focus" not in plan.prompt


CHECKLIST = {"kind": "directive", "text": "Finish:\n- [ ] A\n- [ ] B", "repo": "*", "priority": 1}


@pytest.mark.unit
def test_multiline_items_collapse_to_one_line() -> None:
    text = focus_mod.focus_paragraph("Tools", [CHECKLIST, {**BOARD, "project": "Tools", "acceptance": "a\n\nb"}])
    assert "Finish: - [ ] A - [ ] B" in text
    assert "done when: a b" in text
    assert text.count("\n") == 2  # header + one line per item


@pytest.mark.unit
@pytest.mark.parametrize("items", [[{"kind": "directive", "text": None, "repo": None}], [{"kind": 3, "item": {}}]])
def test_focus_paragraph_is_total(items: list[dict[str, Any]]) -> None:
    assert isinstance(focus_mod.focus_paragraph("Tools", items), str)


@pytest.mark.unit
def test_focus_paragraph_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(item: dict[str, Any]) -> str:
        raise RuntimeError("bad item")

    monkeypatch.setattr(focus_mod, "_line", boom)
    assert focus_mod.focus_paragraph("Tools", [ALL]) == ""


@pytest.mark.unit
def test_plan_survives_a_checklist_directive(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    staff = runner_mod.StaffRunner(roles_loader=lambda: {_role().name: _role()}, focus_loader=lambda: [CHECKLIST])
    plan = staff.plan(runner_mod.RunRequest(role="issue-remediator", repo="Tools", prompt="sweep"))
    assert "Finish: - [ ] A - [ ] B" in plan.prompt
