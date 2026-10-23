"""Skill pointers and RM slice-3 seed catalog (#2157 slice 3)."""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from staff.skills.registry import clear_skills_cache, get_skill, list_skills, skill_validation_errors  # noqa: E402

_SLICE3 = Path(__file__).resolve().parents[1] / "fixtures" / "skills_rm_slice3"


@pytest.fixture
def slice3_env(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_skills_cache()
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(_SLICE3))
    yield
    clear_skills_cache()


def test_slice3_seed_catalog(slice3_env: None) -> None:
    names = {s.name for s in list_skills()}
    assert names >= {
        "fleet-open-pr",
        "fleet-meaningful-tests",
        "upstreamdrift-run-simulation",
        "affinedrift-add-article",
    }
    errors = skill_validation_errors()
    assert any("report-template" in k for k in errors)
    assert "technical-report" not in names


def test_pointer_loads_external_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_skills_cache()
    repos = tmp_path / "repos"
    report = repos / "Report-Template" / "skills" / "technical-report"
    report.mkdir(parents=True)
    (repos / "Report-Template" / ".git").mkdir()
    (report / "SKILL.md").write_text(
        dedent(
            """---
name: technical-report
version: 2.1.0
description: Canonical report skill body.
roles: [barb]
repos: [Report-Template]
task_types: [technical-report]
---
Build the report from templates in this repo.
"""
        ),
        encoding="utf-8",
    )
    skills = tmp_path / "skills" / "report-template" / "technical-report"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        dedent(
            """---
name: technical-report
version: 1.0.0
description: stub
roles: [barb]
repos: [Report-Template]
task_types: [technical-report]
pointer:
  repo: Report-Template
  path: skills/technical-report/SKILL.md
---
"""
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(repos))
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(tmp_path / "skills"))
    detail = get_skill("technical-report")
    assert detail is not None
    assert detail.version == "2.1.0"
    assert "Canonical report skill body" in detail.description
    clear_skills_cache()
