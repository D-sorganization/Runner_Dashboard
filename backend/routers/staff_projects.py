"""Project intake API (#2157 slice 5)."""

# ruff: noqa: B008
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, require_scope
from pydantic import BaseModel, ConfigDict, Field
from staff.project_intake import decompose_project, get_project_store, project_to_dict

router = APIRouter(prefix="/projects", tags=["staff-projects"])


class ProjectCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=4000)
    definition_of_done: str = Field(default="", max_length=4000)
    repos: list[str] = Field(default_factory=list, max_length=20)
    model_config = ConfigDict(extra="forbid")


class ProjectListResponse(BaseModel):
    count: int
    projects: list[dict[str, object]]
    model_config = ConfigDict(extra="forbid")


@router.post("", response_model=dict[str, object])
async def create_project_v1(
    body: ProjectCreateBody,
    peer: Principal = Depends(require_scope("staff.write")),
) -> dict[str, object]:
    store = get_project_store()
    try:
        rec = store.create(
            title=body.title,
            goal=body.goal,
            definition_of_done=body.definition_of_done,
            repos=body.repos,
            created_by=peer.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return project_to_dict(rec)


@router.get("", response_model=ProjectListResponse)
async def list_projects_v1(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, object]:
    rows = get_project_store().list_projects()
    payload = [project_to_dict(r) for r in rows]
    return {"count": len(payload), "projects": payload}


@router.get("/{project_id}", response_model=dict[str, object])
async def get_project_v1(
    project_id: str,
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, object]:
    rec = get_project_store().get(project_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project_to_dict(rec)


@router.post("/{project_id}/decompose", response_model=dict[str, object])
async def decompose_project_v1(
    project_id: str,
    peer: Principal = Depends(require_scope("staff.write")),
) -> dict[str, object]:
    store = get_project_store()
    rec = store.get(project_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="project not found")
    tasks = decompose_project(rec)
    updated = store.save_tasks(project_id, tasks)
    return project_to_dict(updated)
