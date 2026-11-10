"""Slice 2 (#4266): skill selection rank and ``*`` role wildcard (C-01, C-02)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.dispatch_bundle import resolve_dispatch_bundle
from staff.skills.registry import clear_skills_cache

pytestmark = pytest.mark.unit


def _write_skill(
    root: Path,
    *,
    subpath: str,
    name: str,
    version: str,
    roles: list[str],
    body: str = "body",
) -> None:
    pkg = root / subpath
    pkg.mkdir(parents=True)
    role_lines = "\n".join(f'  - "{r}"' if r == "*" else f"  - {r}" for r in roles)
    (pkg / "SKILL.md").write_text(
        f"""---
name: {name}
version: {version}
description: x
roles:
{role_lines}
repos:
  - "*"
task_types:
  - staff-dispatch
---
{body}
""",
        encoding="utf-8",
    )


@pytest.fixture
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clear_skills_cache()
    root = tmp_path / "skills"
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(root))
    yield root
    clear_skills_cache()


def test_wildcard_role_matches_every_lane(skills_root: Path) -> None:
    _write_skill(skills_root, subpath="fleet/wild", name="fleet-wild", version="1.0.0", roles=["*"])
    bundle = resolve_dispatch_bundle(role="ad-hoc", repo="Runner_Dashboard", task_type="staff-dispatch")
    assert bundle.skill_pin.startswith("fleet-wild@")


def test_prefers_role_specific_skill_over_wildcard_and_higher_semver_elsewhere(skills_root: Path) -> None:
    _write_skill(
        skills_root,
        subpath="fleet/high-semver-other",
        name="other-skill",
        version="99.0.0",
        roles=["night-watch"],
        body="other",
    )
    _write_skill(
        skills_root,
        subpath="fleet/wild-generic",
        name="generic-wild",
        version="1.0.0",
        roles=["*"],
        body="wild",
    )
    _write_skill(
        skills_root,
        subpath="fleet/explicit-adhoc",
        name="adhoc-skill",
        version="1.0.0",
        roles=["ad-hoc"],
        body="adhoc body",
    )
    bundle = resolve_dispatch_bundle(role="ad-hoc", repo="Runner_Dashboard", task_type="staff-dispatch")
    assert bundle.skill_pin.startswith("adhoc-skill@")
    assert "adhoc body" in bundle.skill_block


def test_semver_within_same_name_not_across_names(skills_root: Path) -> None:
    _write_skill(
        skills_root,
        subpath="fleet/foo-v1",
        name="same-name",
        version="1.0.0",
        roles=["ad-hoc"],
    )
    _write_skill(
        skills_root,
        subpath="fleet/foo-v2",
        name="same-name",
        version="2.0.0",
        roles=["ad-hoc"],
    )
    _write_skill(
        skills_root,
        subpath="fleet/bar-old",
        name="zebra-skill",
        version="0.1.0",
        roles=["ad-hoc"],
    )
    bundle = resolve_dispatch_bundle(role="ad-hoc", repo="Runner_Dashboard", task_type="staff-dispatch")
    assert bundle.skill_version == "2.0.0"
    assert bundle.skill_pin.startswith("same-name@2.0.0")
