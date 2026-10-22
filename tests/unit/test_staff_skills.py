"""Skill registry and API tests (epic #2157 slice 1)."""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from identity import Principal, require_principal, require_scope  # noqa: E402
from server import app  # noqa: E402
from staff.skills.contract import SkillValidationError, parse_skill_md  # noqa: E402
from staff.skills.registry import clear_skills_cache, get_skill, list_skills, skill_validation_errors  # noqa: E402

_PRINCIPAL = Principal(id="op", type="human", name="Op", roles=["operator"], scopes=["staff.read"])


def _write_skill_tree(root: Path) -> None:
    ad = root / "affinedrift/add-article"
    ad.mkdir(parents=True)
    (ad / "templates").mkdir()
    (ad / "requirements").mkdir()
    (ad / "templates/article-quarto.md").write_text("# draft\n", encoding="utf-8")
    (ad / "requirements/citations.md").write_text("Cite sources.\n", encoding="utf-8")
    (ad / "SKILL.md").write_text(
        dedent(
            """---
name: affinedrift-add-article
version: 1.0.0
description: Add an AffineDrift article.
roles: [content-author]
repos: [AffineDrift]
task_types: [add-article]
attachments:
  - {path: templates/article-quarto.md, kind: template}
  - {path: requirements/citations.md, kind: requirements}
---
Run Quarto checks.
"""
        ),
        encoding="utf-8",
    )
    ud = root / "upstreamdrift/run-simulation"
    ud.mkdir(parents=True)
    (ud / "fixtures").mkdir()
    (ud / "fixtures/minimal-run.json").write_text('{"steps": 100}\n', encoding="utf-8")
    (ud / "SKILL.md").write_text(
        dedent(
            """---
name: upstreamdrift-run-simulation
version: 0.2.1
when_to_use: Validate engine output against fixtures.
roles: [simulation-engineer]
repos: [UpstreamDrift]
task_types: [run-simulation]
attachments:
  - {path: fixtures/minimal-run.json, kind: fixture}
---
Run the engine.
"""
        ),
        encoding="utf-8",
    )
    bad = root / "invalid/missing-version"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text(
        "---\nname: broken-skill\ndescription: x\nroles: [content-author]\nrepos: [AffineDrift]\n"
        "task_types: [add-article]\n---\n",
        encoding="utf-8",
    )


@pytest.fixture
def skills_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clear_skills_cache()
    root = tmp_path / "skills"
    _write_skill_tree(root)
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(root))
    yield root
    clear_skills_cache()


@pytest.fixture(autouse=True)
def api_auth() -> Any:
    app.dependency_overrides[require_principal] = lambda: _PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: _PRINCIPAL
    yield
    app.dependency_overrides.clear()


def test_parse_rejects_missing_version(skills_env: Path) -> None:
    text = (skills_env / "invalid/missing-version/SKILL.md").read_text(encoding="utf-8")
    with pytest.raises(SkillValidationError, match="version"):
        parse_skill_md(text, source_path="invalid/missing-version/SKILL.md")


def test_registry_filters_and_pin(skills_env: Path) -> None:
    assert {s.name for s in list_skills()} == {"affinedrift-add-article", "upstreamdrift-run-simulation"}
    assert list_skills(repo="AffineDrift", task_type="add-article")[0].pin.startswith("affinedrift-add-article@1.0.0+")
    detail = get_skill("affinedrift-add-article")
    assert detail and len(detail.attachments) == 2
    assert any("missing-version" in k for k in skill_validation_errors())
    assert get_skill("upstreamdrift-run-simulation", version="9.9.9") is None


def test_skills_api(skills_env: Path) -> None:
    client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"}, raise_server_exceptions=False)
    listed = client.get("/api/v1/staff/skills", params={"repo": "UpstreamDrift"}).json()
    assert listed["count"] == 1
    got = client.get("/api/v1/staff/skills/affinedrift-add-article").json()
    assert got["attachments"][0]["content_hash"]
    assert client.get("/api/v1/staff/skills/none").status_code == 404
