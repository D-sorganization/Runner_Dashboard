"""Dispatch bundle: standards + skill matching (#2157 slice 2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from staff import roles as roles_mod  # noqa: E402
from staff.dispatch_bundle import (  # noqa: E402
    DEFAULT_GENERIC_SKILL,
    DispatchBundle,
    DispatchContractError,
    resolve_dispatch_bundle,
)
from staff.skills.registry import clear_skills_cache  # noqa: E402

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "skills_dispatch"
pytestmark = pytest.mark.unit


def test_context_stamp_separates_injected_context_from_full_prompt_estimate() -> None:
    bundle = DispatchBundle(
        skill_pin="skill@1.0.0",
        skill_version="1.0.0",
        standards_version="1.1.0",
        standards_pin="fleet-standards@1.1.0#abc",
        skill_block="skill instructions",
        standards_block="fleet standards",
        standards_tokens=10,
    )
    stamp = bundle.context_stamp(prompt="task prompt " * 40, lessons_tokens=3)
    estimates = stamp["token_estimates"]
    assert estimates["total_injected"] == 10 + estimates["tier1_skills"] + 3
    assert estimates["full_prompt"] > estimates["total_injected"]


@pytest.fixture(autouse=True)
def _skills_env(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_skills_cache()
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(_FIXTURE_ROOT))
    yield
    clear_skills_cache()


def test_resolve_injects_versions() -> None:
    bundle = resolve_dispatch_bundle(role="night-watch", repo="AffineDrift")
    assert bundle.standards_version == "1.0.0"
    assert bundle.skill_pin.startswith("fleet-staff-unattended@1.0.0+")
    assert "TDD" in bundle.standards_block
    assert "unattended" in bundle.skill_block.lower()


def test_ad_hoc_runner_dashboard_regression() -> None:
    """Live hub 6613792c: ad-hoc + Runner_Dashboard must not 422 on skill match."""
    bundle = resolve_dispatch_bundle(role="ad-hoc", repo="Runner_Dashboard", task_type="")
    assert bundle.skill_pin.startswith(f"{DEFAULT_GENERIC_SKILL}@")


def test_falls_back_to_generic_when_role_not_listed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "skills"
    pkg = root / "fleet" / "staff-unattended"
    pkg.mkdir(parents=True)
    (pkg / "SKILL.md").write_text(
        """---
name: fleet-staff-unattended
version: 1.0.0
description: generic
roles:
  - night-watch
repos:
  - "*"
task_types:
  - staff-dispatch
---
Generic body.
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(root))
    clear_skills_cache()
    bundle = resolve_dispatch_bundle(role="ad-hoc", repo="Runner_Dashboard")
    assert "Generic body" in bundle.skill_block


def test_proceeds_without_skill_when_registry_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_SKILLS_DIR", "/nonexistent/skills")
    clear_skills_cache()
    bundle = resolve_dispatch_bundle(role="unknown-role", repo="AffineDrift")
    assert bundle.skill_pin == ""
    assert bundle.skill_block == ""
    assert bundle.standards_version == "1.0.0"


def test_rejects_unknown_explicit_skill() -> None:
    with pytest.raises(DispatchContractError, match="does not exist"):
        resolve_dispatch_bundle(role="ad-hoc", repo="Runner_Dashboard", requested_skill="no-such-skill")


def test_task_type_filters_matching_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "skills"
    for name, roles, tasks in (
        ("role-skill", ("ad-hoc",), ("open-pr",)),
        ("fleet-staff-unattended", ("ad-hoc",), ("staff-dispatch",)),
    ):
        pkg = root / "fleet" / name
        pkg.mkdir(parents=True)
        role_lines = "\n".join(f"  - {r}" for r in roles)
        task_lines = "\n".join(f"  - {t}" for t in tasks)
        (pkg / "SKILL.md").write_text(
            f"""---
name: {name}
version: 1.0.0
description: x
roles:
{role_lines}
repos:
  - "*"
task_types:
{task_lines}
---
Body {name}.
""",
            encoding="utf-8",
        )
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(root))
    clear_skills_cache()
    bundle = resolve_dispatch_bundle(role="ad-hoc", repo="Runner_Dashboard", task_type="open-pr")
    assert bundle.skill_pin.startswith("role-skill@")


def test_every_staff_role_yml_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    for role in (
        "ad-hoc",
        "pr-remediator",
        "issue-remediator",
        "night-watch",
        "barb",
        "project-steward",
        "code-reviewer",
    ):
        (roles_dir / f"{role}.yml").write_text(
            f"name: {role}\ntitle: {role}\nproviders: [claude]\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    roles_mod.clear_roles_cache()
    loaded = roles_mod.load_roles(roles_dir)
    for role_name in loaded:
        bundle = resolve_dispatch_bundle(
            role=role_name,
            repo="Runner_Dashboard",
            task_type="staff-dispatch",
        )
        assert bundle.standards_version
        assert bundle.skill_pin or bundle.skill_block == ""
