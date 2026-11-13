"""rd-barb playbook names only live Repository_Management skills (#3649)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PLAYBOOK = Path(__file__).resolve().parents[2] / "docs" / "agents" / "rd-barb.md"
# expert-panel-consultation merged into `panel` (RM #2596); `goal` renamed `fleet-goal` (RM #2612).
RETIRED = ("expert-panel-consultation", "goal")


def _skills_section() -> str:
    text = PLAYBOOK.read_text(encoding="utf-8")
    return text.split("## Skills", 1)[1].split("\n## ", 1)[0]


def test_skills_section_names_no_retired_skill() -> None:
    named = set(re.findall(r"`([a-z][a-z0-9-]*)`", _skills_section()))
    assert named.isdisjoint(RETIRED), sorted(named & set(RETIRED))


def test_skills_section_names_current_goal_and_panel() -> None:
    named = set(re.findall(r"`([a-z][a-z0-9-]*)`", _skills_section()))
    assert {"fleet-goal", "panel"} <= named
