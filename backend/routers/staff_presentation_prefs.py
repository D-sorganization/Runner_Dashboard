"""Personal presentation-preferences API (#3495): display label, role order
and avatar overrides, keyed by the authenticated operator. Reuses existing
identity/role contracts; never exposes a way to change role ids, scopes,
holds, instructions, routing, schedule or activation.
"""

# ruff: noqa: B008
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, require_scope
from pydantic import BaseModel, ConfigDict, Field
from staff.presentation_prefs import (
    PreferencesConflictError,
    PreferencesValidationError,
    get_presentation_prefs_store,
)
from staff.roles import load_roles

router = APIRouter(prefix="/api/v1/staff/presentation-prefs", tags=["staff-presentation-prefs"])


class PresentationPrefsResponse(BaseModel):
    operator_id: str
    display_label: str
    role_order: list[str]
    avatar_overrides: dict[str, str]
    revision: int
    updated_at: str
    model_config = ConfigDict(extra="forbid")


class PresentationPrefsSaveBody(BaseModel):
    expected_revision: int = Field(ge=0)
    display_label: str = Field(default="", max_length=200)
    role_order: list[str] = Field(default_factory=list, max_length=500)
    avatar_overrides: dict[str, str] = Field(default_factory=dict)
    model_config = ConfigDict(extra="forbid")


@router.get("", response_model=PresentationPrefsResponse)
async def get_presentation_prefs_v1(
    peer: Principal = Depends(require_scope("staff.read")),
) -> PresentationPrefsResponse:
    prefs = get_presentation_prefs_store().get(peer.id)
    return PresentationPrefsResponse(**prefs.to_dict())


@router.put("", response_model=PresentationPrefsResponse)
async def save_presentation_prefs_v1(
    body: PresentationPrefsSaveBody,
    peer: Principal = Depends(require_scope("staff.write")),
) -> PresentationPrefsResponse:
    known_role_ids = frozenset(load_roles().keys())
    try:
        prefs = get_presentation_prefs_store().save(
            peer.id,
            expected_revision=body.expected_revision,
            display_label=body.display_label,
            role_order=body.role_order,
            avatar_overrides=body.avatar_overrides,
            known_role_ids=known_role_ids,
        )
    except PreferencesConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PreferencesValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PresentationPrefsResponse(**prefs.to_dict())


@router.post("/reset", response_model=PresentationPrefsResponse)
async def reset_presentation_prefs_v1(
    peer: Principal = Depends(require_scope("staff.write")),
) -> PresentationPrefsResponse:
    get_presentation_prefs_store().reset(peer.id)
    prefs = get_presentation_prefs_store().get(peer.id)
    return PresentationPrefsResponse(**prefs.to_dict())
