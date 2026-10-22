"""Resolve skills + standards for an unattended staff dispatch (#2157 slice 2)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from staff.skills.contract import SkillDetail, semver_sort_key
from staff.skills.registry import get_skill, list_skills, repo_matches_skill
from staff.standards_registry import FleetStandards, load_fleet_standards

log = logging.getLogger("dashboard.staff.dispatch_bundle")

DEFAULT_GENERIC_SKILL = "fleet-staff-unattended"


class DispatchContractError(ValueError):
    """Dispatch cannot proceed without fleet standards or an explicitly missing skill."""


@dataclass(frozen=True)
class DispatchBundle:
    skill_pin: str
    skill_version: str
    standards_version: str
    standards_pin: str
    skill_block: str
    standards_block: str


def _task_matches(skill_tasks: tuple[str, ...], task_type: str) -> bool:
    if not task_type:
        return True
    return task_type in skill_tasks


def match_skills_for_dispatch(role: str, repo: str, task_type: str = "") -> list:
    rows = list_skills(role=role, repo=None, task_type=None)
    return [
        s
        for s in rows
        if role in s.roles and repo_matches_skill(s.repos, repo) and _task_matches(s.task_types, task_type)
    ]


def _standards_block(standards: FleetStandards, standards_extra: str) -> str:
    standards_body = standards.body.strip()
    if standards_extra.strip():
        standards_body = standards_body + "\n\nDispatch-specific standards:\n" + standards_extra.strip()
    return f"Fleet standards ({standards.pin}):\n{standards_body}"


def _bundle_from_detail(
    detail: SkillDetail,
    standards: FleetStandards,
    standards_extra: str,
) -> DispatchBundle:
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
        standards_block=_standards_block(standards, standards_extra),
    )


def _bundle_without_skill(standards: FleetStandards, standards_extra: str) -> DispatchBundle:
    return DispatchBundle(
        skill_pin="",
        skill_version="",
        standards_version=standards.version,
        standards_pin=standards.pin,
        skill_block="",
        standards_block=_standards_block(standards, standards_extra),
    )


def resolve_dispatch_bundle(
    *,
    role: str,
    repo: str,
    task_type: str = "",
    standards_extra: str = "",
    requested_skill: str | None = None,
) -> DispatchBundle:
    """Pre: role is non-empty. Post: bundle for prompt injection or DispatchContractError.

    When no skill matches the role/repo/task_type tuple, dispatch falls back to the
    generic ``fleet-staff-unattended`` skill, then to standards-only (``skill_pin`` empty).
    A non-empty ``requested_skill`` must exist in the registry or dispatch is rejected.
    """
    if not role.strip():
        raise DispatchContractError("role is required")
    standards: FleetStandards = load_fleet_standards()
    if not standards.version:
        raise DispatchContractError("fleet standards version is unavailable")

    role_key = role.strip()
    repo_key = repo.strip()
    task_key = task_type.strip()

    if requested_skill and requested_skill.strip():
        name = requested_skill.strip()
        detail = get_skill(name)
        if detail is None:
            raise DispatchContractError(f"requested skill {name!r} does not exist")
        return _bundle_from_detail(detail, standards, standards_extra)

    matches = match_skills_for_dispatch(role_key, repo_key, task_key)
    if matches:
        best = max(matches, key=lambda s: semver_sort_key(s.version))
        detail = get_skill(best.name, version=best.version)
        if detail is None:
            raise DispatchContractError(f"skill {best.name}@{best.version} is indexed but unreadable")
        return _bundle_from_detail(detail, standards, standards_extra)

    generic = get_skill(DEFAULT_GENERIC_SKILL)
    if generic is not None:
        log.warning(
            "no skill matches role=%r repo=%r task_type=%r; using generic %s",
            role_key,
            repo_key,
            task_key,
            DEFAULT_GENERIC_SKILL,
        )
        return _bundle_from_detail(generic, standards, standards_extra)

    log.warning(
        "no skill matches role=%r repo=%r task_type=%r and no generic skill; proceeding without skill package",
        role_key,
        repo_key,
        task_key,
    )
    return _bundle_without_skill(standards, standards_extra)
