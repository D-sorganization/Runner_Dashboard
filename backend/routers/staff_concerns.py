"""Staff Concerns API routes (/api/v1/staff/concerns) (#2237)."""

# ruff: noqa: B008
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from identity import Principal, require_scope
from pydantic import BaseModel, Field
from staff import concerns
from staff.conversations import get_conversation_store
from staff.idempotency import require_idempotency_header

router = APIRouter(prefix="/concerns", tags=["staff-concerns"])


class ConcernCreateBody(BaseModel):
    kind: concerns.ConcernKind
    repo: str
    summary: str = Field(max_length=concerns.SUMMARY_MAX_CHARS)
    detail: str = Field(max_length=concerns.DETAIL_MAX_CHARS)
    severity: concerns.ConcernSeverity = "medium"
    ask: str = ""
    issue: int | None = None
    pr: int | None = None
    run_id: str | None = None
    session_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class ConcernResolveBody(BaseModel):
    status: Literal["resolved", "declined"]
    reply: str
    proposal: dict[str, Any] | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
def post_concern(
    body: ConcernCreateBody,
    response: Response,
    caller: Principal = Depends(require_scope("staff.chat")),
    idempotency_key: str = Depends(require_idempotency_header),
) -> dict[str, Any]:
    try:
        rec, is_new = concerns.create_or_dedup_concern(
            get_conversation_store(),
            author=caller.id or caller.name or "unknown",
            idempotency_key=idempotency_key,
            **body.model_dump(),
        )
    except concerns.SecretBearingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except concerns.RateLimitError as exc:
        response.headers["Retry-After"] = str(exc.retry_after)
        detail = {"message": str(exc), "retry_after": exc.retry_after}
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=detail, headers=response.headers) from exc
    if not is_new:
        response.status_code = status.HTTP_200_OK
    return rec.to_dict()


@router.get("")
def get_concerns(
    status_filter: concerns.ConcernStatus | None = Query(default=None, alias="status"),
    repo: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    _caller: Principal = Depends(require_scope("staff.read")),
) -> list[dict[str, Any]]:
    return [
        r.to_dict()
        for r in concerns.list_concerns(get_conversation_store(), status=status_filter, repo=repo, limit=limit)
    ]


@router.get("/{concern_id}")
def get_concern_by_id(concern_id: str, _caller: Principal = Depends(require_scope("staff.read"))) -> dict[str, Any]:
    rec = concerns.get_concern(get_conversation_store(), concern_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Concern not found")
    return rec.to_dict()


@router.post("/{concern_id}/resolve")
def resolve_concern_endpoint(
    concern_id: str, body: ConcernResolveBody, caller: Principal = Depends(require_scope("staff.chat"))
) -> dict[str, Any]:
    try:
        rec = concerns.resolve_concern(
            get_conversation_store(),
            concern_id,
            status=body.status,
            reply=body.reply,
            resolved_by=caller.id or caller.name or "unknown",
            proposal=body.proposal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return rec.to_dict()
