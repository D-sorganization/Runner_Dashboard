"""HTTP routes for the Librarian Intake Queue (chat attachment staging & promotion)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from identity import Principal
from library.intake import (
    decode_base64_payload,
    list_intake_items,
    promote_intake_item,
    stage_intake_item,
)
from pydantic import BaseModel, Field

intake_router = APIRouter(tags=["library"])


def _db() -> Path:
    import routers.library as lib_router

    return lib_router._db()


def _require_peer(request: Request) -> Principal:
    import routers.library as lib_router

    return lib_router.require_library_peer(
        request,
        header_token=request.headers.get("Authorization"),
        cookie_token=request.cookies.get("hub_session"),
    )


class LibraryIntakeCreateRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=240)
    content_base64: str = Field(..., min_length=0)
    mime_type: str = Field(default="application/octet-stream", max_length=160)
    size_bytes: int | None = Field(default=None, ge=0)
    sensitivity: str | None = Field(default="confidential_work", max_length=64)
    thread_id: str | None = Field(default=None, max_length=120)


@intake_router.post("/intake")
def library_intake_stage_route(
    payload: LibraryIntakeCreateRequest,
    principal: Principal = Depends(_require_peer),  # noqa: B008
) -> dict[str, Any]:
    try:
        raw = decode_base64_payload(payload.content_base64)
        return stage_intake_item(
            _db(),
            filename=payload.filename,
            content=raw,
            mime_type=payload.mime_type,
            requested_sensitivity=payload.sensitivity,
            thread_id=payload.thread_id,
            uploaded_by=principal.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@intake_router.get("/intake")
def library_intake_list_route(
    status: str | None = Query(None, max_length=64),
    sensitivity: str | None = Query(None, max_length=64),
    _principal: Principal = Depends(_require_peer),  # noqa: B008
) -> dict[str, Any]:
    items = list_intake_items(_db(), status=status, sensitivity=sensitivity)
    return {"items": items, "count": len(items)}


@intake_router.post("/intake/{intake_id}/promote")
def library_intake_promote_route(
    intake_id: int,
    principal: Principal = Depends(_require_peer),  # noqa: B008
) -> dict[str, Any]:
    try:
        return promote_intake_item(_db(), intake_id, promoted_by=principal.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
