"""Resolve skills + standards for an unattended staff dispatch (#2157 slice 2)."""

from __future__ import annotations

from dataclasses import dataclass

from staff.skills.contract import SkillDetail, semver_sort_key
from staff.skills.registry import get_skill, list_skills
from staff.standards_registry import FleetStandards, load_fleet_standards


class DispatchContractError(ValueError):
    """Dispatch cannot proceed without a matching skill and standards version."""


@dataclass(frozen=True)
class DispatchBundle:
    skill_pin: str
    skill_version: str
    standards_version: str
    standards_pin: str
    skill_block: str
    standards_block: str


def _repo_matches(skill_repos: tuple[str, ...], repo: str) -> bool:
    if not skill_repos or "*" in skill_repos:
        return True
    return bool(repo) and repo in skill_repos


def _task_matches(skill_tasks: tuple[str, ...], task_type: str) -> bool:
    if not task_type:
        return True
    return task_type in skill_tasks


def match_skills_for_dispatch(role: str, repo: str, task_type: str = "") -> list:
    rows = list_skills(role=role, repo=None, task_type=None)
    return [
        s for s in rows if role in s.roles and _repo_matches(s.repos, repo) and _task_matches(s.task_types, task_type)
    ]


def resolve_dispatch_bundle(
    *,
    role: str,
    repo: str,
    task_type: str = "",
    standards_extra: str = "",
) -> DispatchBundle:
    """Pre: role is non-empty. Post: bundle for prompt injection or DispatchContractError."""
    if not role.strip():
        raise DispatchContractError("role is required")
    standards: FleetStandards = load_fleet_standards()
    if not standards.version:
        raise DispatchContractError("fleet standards version is unavailable")

    matches = match_skills_for_dispatch(role.strip(), repo.strip(), task_type.strip())
    if not matches:
        raise DispatchContractError(
            f"no skill matches role={role!r} repo={repo!r} task_type={task_type!r}; dispatch rejected (DbC)"
        )
    best = max(matches, key=lambda s: semver_sort_key(s.version))
    detail: SkillDetail | None = get_skill(best.name, version=best.version)
    if detail is None:
        raise DispatchContractError(f"skill {best.name}@{best.version} is indexed but unreadable")

    standards_body = standards.body.strip()
    if standards_extra.strip():
        standards_body = standards_body + "\n\nDispatch-specific standards:\n" + standards_extra.strip()
    skill_block = f"Skill package ({detail.pin}):\n{detail.body.strip()}"
    if detail.attachments:
        names = ", ".join(a.path for a in detail.attachments)
        skill_block += f"\n\n(Attachments bundled in registry: {names})"

    return DispatchBundle(
        skill_pin=detail.pin,
        skill_version=detail.version,
        standards_version=standards.version,
        standards_pin=standards.pin,
        skill_block=skill_block,
        standards_block=f"Fleet standards ({standards.pin}):\n{standards_body}",
    )
