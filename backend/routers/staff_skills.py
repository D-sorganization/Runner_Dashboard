"""Read API for the fleet skill registry (epic #2157 slice 1)."""

# ruff: noqa: B008
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, require_scope
from pydantic import BaseModel, ConfigDict, Field
from staff.skills.contract import SkillSummary
from staff.skills.registry import get_skill, list_skills

router = APIRouter(prefix="/skills", tags=["staff-skills"])


class StaffSkillAttachment(BaseModel):
    path: str
    kind: str
    content: str
    content_hash: str


class StaffSkillSummary(BaseModel):
    name: str
    version: str
    description: str = ""
    when_to_use: str = ""
    roles: list[str] = Field(default_factory=list)
    repos: list[str] = Field(default_factory=list)
    task_types: list[str] = Field(default_factory=list)
    content_hash: str
    pin: str
    source_path: str = ""
    model_config = ConfigDict(extra="forbid")


class StaffSkillListResponse(BaseModel):
    count: int
    skills: list[StaffSkillSummary]
    model_config = ConfigDict(extra="forbid")


class StaffSkillDetailResponse(StaffSkillSummary):
    body: str
    attachments: list[StaffSkillAttachment] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


def _summary(skill: SkillSummary) -> dict[str, object]:
    return {
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "when_to_use": skill.when_to_use,
        "roles": list(skill.roles),
        "repos": list(skill.repos),
        "task_types": list(skill.task_types),
        "content_hash": skill.content_hash,
        "pin": skill.pin,
        "source_path": skill.source_path,
    }


@router.get("", response_model=StaffSkillListResponse)
async def list_skills_v1(
    role: str | None = Query(default=None),
    repo: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, object]:
    rows = list_skills(role=role, repo=repo, task_type=task_type)
    return {"count": len(rows), "skills": [_summary(r) for r in rows]}


@router.get("/{name}", response_model=StaffSkillDetailResponse)
async def get_skill_v1(
    name: str,
    version: str | None = Query(default=None),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, object]:
    skill = get_skill(name, version=version)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return {
        **_summary(skill),
        "body": skill.body,
        "attachments": [
            {"path": a.path, "kind": a.kind, "content": a.content, "content_hash": a.content_hash}
            for a in skill.attachments
        ],
    }
