"""Fleet friction ledger and owner-questions queue (/api/v1/fleet/friction, /api/v1/fleet/owner-questions).

Dashboard home for the shell orchestrator's FRICTION_LEDGER.md and OWNER_QUESTIONS.md.

Auth: reads use ``require_fleet_peer``; writes need ``coordination.write`` (orchestrator and
bot principals, operators); answering an owner question needs ``staff.admin`` (operator/admin only).
A friction row can be CLOSED only with a non-empty ``fix_ref`` and ``detector`` (422 otherwise).
"""

# ruff: noqa: B008
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, principal_has_scope, require_fleet_peer, require_scope
from pydantic import BaseModel, Field
from staff import friction_store as store

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet-friction"])

FrictionStatus = Literal["open", "in_progress", "waiting", "closed"]
QuestionStatus = Literal["open", "answered", "withdrawn"]


class FrictionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    seen: str = Field(min_length=1, max_length=8000)
    repo: str | None = Field(default=None, max_length=200)
    fix: str = Field(default="", max_length=8000)
    fix_ref: str = Field(default="", max_length=500)
    detector: str = Field(default="", max_length=4000)
    detector_ref: str = Field(default="", max_length=500)
    status: FrictionStatus = "open"


class FrictionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    seen: str | None = Field(default=None, min_length=1, max_length=8000)
    repo: str | None = Field(default=None, max_length=200)
    fix: str | None = Field(default=None, max_length=8000)
    fix_ref: str | None = Field(default=None, max_length=500)
    detector: str | None = Field(default=None, max_length=4000)
    detector_ref: str | None = Field(default=None, max_length=500)
    status: FrictionStatus | None = None


class FrictionItem(BaseModel):
    id: str
    title: str
    seen: str
    repo: str | None = None
    fix: str = ""
    fix_ref: str = ""
    detector: str = ""
    detector_ref: str = ""
    status: FrictionStatus
    created_by: str = ""
    created_at: str
    updated_at: str
    recurrence_count: int = 0
    last_seen_at: str | None = None


class FrictionList(BaseModel):
    items: list[FrictionItem]
    count: int


class QuestionCreate(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    repo: str | None = Field(default=None, max_length=200)
    issue: int | None = Field(default=None, ge=1)
    options: list[str] = Field(default_factory=list, max_length=20)
    recommended: str | None = Field(default=None, max_length=500)


class QuestionAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)


class QuestionItem(BaseModel):
    id: str
    repo: str | None = None
    issue: int | None = None
    question: str
    options: list[str] = Field(default_factory=list)
    recommended: str | None = None
    asked_by: str = ""
    asked_at: str
    answer: str | None = None
    answered_by: str | None = None
    answered_at: str | None = None
    status: QuestionStatus


class QuestionList(BaseModel):
    items: list[QuestionItem]
    count: int


def _not_found(exc: store.NotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=f"not found: {exc}")


# -- friction ---------------------------------------------------------------


@router.get("/friction", response_model=FrictionList)
async def list_friction(
    status: FrictionStatus | None = Query(default=None),
    repo: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=200, ge=1, le=1000),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    items = store.list_friction(status=status, repo=repo, limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/friction", response_model=FrictionItem, status_code=201)
async def create_friction(
    body: FrictionCreate,
    who: Principal = Depends(require_scope("coordination.write")),
) -> dict[str, Any]:
    try:
        return store.create_friction(created_by=who.id, **body.model_dump())
    except store.FrictionRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/friction/{friction_id}", response_model=FrictionItem)
async def update_friction(
    friction_id: str,
    body: FrictionUpdate,
    _who: Principal = Depends(require_scope("coordination.write")),
) -> dict[str, Any]:
    try:
        return store.update_friction(friction_id, body.model_dump(exclude_unset=True, exclude_none=True))
    except store.FrictionRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except store.NotFoundError as exc:
        raise _not_found(exc) from exc


@router.post("/friction/{friction_id}/recurrence", response_model=FrictionItem)
async def friction_recurrence(
    friction_id: str,
    _who: Principal = Depends(require_scope("coordination.write")),
) -> dict[str, Any]:
    """Detectors call this when a closed or open friction recurs: increments the count, stamps last_seen_at."""
    try:
        return store.record_recurrence(friction_id)
    except store.NotFoundError as exc:
        raise _not_found(exc) from exc


# -- owner questions --------------------------------------------------------


@router.get("/owner-questions", response_model=QuestionList)
async def list_owner_questions(
    status: QuestionStatus | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    items = store.list_questions(status=status, limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/owner-questions", response_model=QuestionItem, status_code=201)
async def ask_owner_question(
    body: QuestionCreate,
    who: Principal = Depends(require_scope("coordination.write")),
) -> dict[str, Any]:
    return store.create_question(asked_by=who.id, **body.model_dump())


@router.post("/owner-questions/{question_id}/answer", response_model=QuestionItem)
async def answer_owner_question(
    question_id: str,
    body: QuestionAnswer,
    who: Principal = Depends(require_scope("staff.admin")),
) -> dict[str, Any]:
    try:
        return store.answer_question(question_id, body.answer, who.id)
    except store.NotFoundError as exc:
        raise _not_found(exc) from exc
    except store.StateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/owner-questions/{question_id}/withdraw", response_model=QuestionItem)
async def withdraw_owner_question(
    question_id: str,
    who: Principal = Depends(require_scope("coordination.write")),
) -> dict[str, Any]:
    try:
        current = store.get_question(question_id)
        if current["asked_by"] != who.id and not principal_has_scope(who, "staff.admin"):
            raise HTTPException(status_code=403, detail="only the asker or an operator may withdraw")
        return store.withdraw_question(question_id, who.id)
    except store.NotFoundError as exc:
        raise _not_found(exc) from exc
    except store.StateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
