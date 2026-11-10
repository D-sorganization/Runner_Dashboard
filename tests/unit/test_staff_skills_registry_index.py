"""Slice 4 (#4266 C-06, C-16): top-level RM skills and malformed SKILL.md isolation."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.skills.registry import clear_skills_cache, get_skill, list_skills, skill_validation_errors

pytestmark = pytest.mark.unit


@pytest.fixture
def skills_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clear_skills_cache()
    root = tmp_path / "skills"
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(root))
    yield root
    clear_skills_cache()


def test_indexes_top_level_skill_package(skills_env: Path) -> None:
    pkg = skills_env / "backlog-push"
    pkg.mkdir(parents=True)
    (pkg / "SKILL.md").write_text(
        """---
name: backlog-push
version: 1.0.0
description: push
roles:
  - backlog-push
repos:
  - "*"
task_types:
  - staff-dispatch
---
Push backlog.
""",
        encoding="utf-8",
    )
    names = {s.name for s in list_skills()}
    assert "backlog-push" in names
    detail = get_skill("backlog-push")
    assert detail is not None and "Push backlog" in detail.body


def test_malformed_skill_skipped_without_poisoning_cache(skills_env: Path, caplog: pytest.LogCaptureFixture) -> None:
    good = skills_env / "fleet" / "good-skill"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text(
        """---
name: good-skill
version: 1.0.0
description: ok
roles:
  - ad-hoc
repos:
  - "*"
task_types:
  - staff-dispatch
---
Good body.
""",
        encoding="utf-8",
    )
    bad = skills_env / "fleet" / "broken"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("not yaml frontmatter\n", encoding="utf-8")
    assert get_skill("good-skill") is not None
    assert {s.name for s in list_skills()} == {"good-skill"}
    errors = skill_validation_errors()
    assert errors
    assert any("broken" in key for key in errors)
