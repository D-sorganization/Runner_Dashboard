"""Staff concerns and agent escalation router (#2237)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from identity import Principal, require_scope
from staff.concerns import (
    RateLimitExceededError,
    SecretDetectedError,
    get_concern,
    list_concerns,
    resolve_concern,
    submit_concern,
)
from staff.conversations import get_conversation_store

router = APIRouter(tags=["staff-concerns"], include_in_schema=False)
_CHAT, _READ = require_scope("staff.chat"), require_scope("staff.read")


@router.post("/concerns")
async def create_concern(
    body: dict[str, Any],
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    caller: Principal = Depends(_CHAT),  # noqa: B008
) -> dict[str, Any]:
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    try:
        res, is_dup = submit_concern(
            get_conversation_store(),
            author=caller.id,
            idempotency_key=idempotency_key.strip(),
            data=body,
        )
    except SecretDetectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RateLimitExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    response.status_code = 200 if is_dup else 201
    return res


@router.get("/concerns")
async def get_concerns(request: Request, _c: Principal = Depends(_READ)) -> dict[str, Any]:  # noqa: B008
    q = request.query_params
    try:
        limit = min(int(q.get("limit", 50)), 100)
    except ValueError:
        limit = 50
    items = list_concerns(get_conversation_store(), status=q.get("status"), repo=q.get("repo"), limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/concerns/{concern_id}")
async def get_concern_by_id(concern_id: str, _c: Principal = Depends(_READ)) -> dict[str, Any]:  # noqa: B008
    c = get_concern(get_conversation_store(), concern_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Concern not found")
    return c


@router.post("/concerns/{concern_id}/resolve")
async def resolve_concern_endpoint(
    concern_id: str,
    body: dict[str, Any],
    _c: Principal = Depends(_CHAT),  # noqa: B008
) -> dict[str, Any]:
    res = resolve_concern(
        get_conversation_store(),
        concern_id,
        status=body.get("status", "resolved"),
        reply=body.get("reply", ""),
        action=body.get("action"),
        action_params=body.get("action_params", {}),
    )
    if res is None:
        raise HTTPException(status_code=404, detail="Concern not found")
    return res
