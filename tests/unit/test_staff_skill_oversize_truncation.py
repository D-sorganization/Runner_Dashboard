"""Slice 3 (#4266 C-03): oversize skill bodies truncate instead of failing dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff import workspace
from staff.dispatch_bundle import DispatchBundle, resolve_dispatch_bundle
from staff.roles import RoleSpec
from staff.skills.registry import clear_skills_cache

pytestmark = pytest.mark.unit


@pytest.fixture
def skills_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clear_skills_cache()
    root = tmp_path / "skills"
    pkg = root / "fleet" / "huge-skill"
    pkg.mkdir(parents=True)
    body = "X" * 20_000
    (pkg / "SKILL.md").write_text(
        f"""---
name: huge-skill
version: 1.0.0
description: big
roles:
  - ad-hoc
repos:
  - "*"
task_types:
  - staff-dispatch
---
{body}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(root))
    yield root
    clear_skills_cache()


def test_oversize_skill_truncates_with_pin_pointer(skills_env: Path) -> None:
    bundle = resolve_dispatch_bundle(role="ad-hoc", repo="Runner_Dashboard", task_type="staff-dispatch")
    assert bundle.skill_pin.startswith("huge-skill@")
    role = RoleSpec(name="ad-hoc", title="Ad hoc")
    prompt = workspace.compose_prompt(
        role,
        repo="Runner_Dashboard",
        target_ref="#1",
        operator_prompt="task",
        branch="b1",
        dispatch_bundle=bundle,
        lessons=(),
    )
    assert "registry pin:" in prompt
    assert bundle.skill_pin in prompt
    assert "X" * 20_000 not in prompt
