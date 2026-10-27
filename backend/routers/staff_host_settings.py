"""Staff Hub host-settings API (issue #3101).

Routes (mounted under ``/api/staff`` and ``/api/v1/staff``):
  GET  /host-settings   Global + per-role settings with resolved leaders.
  PUT  /host-settings   Replace settings (``confirmed: true`` required).
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, Field
from staff.host_settings_contract import HostSettingsDocument, resolve_all_roles
from staff.host_settings_service import HostSettingsError, apply_host_settings_update
from staff.host_settings_contract import host_settings_path, load_host_settings
from staff.roles import load_roles

log = logging.getLogger("dashboard.staff.host_settings")
router = APIRouter(tags=["staff"])


class HostSettingsPutBody(BaseModel):
    schema_version: int = Field(default=1)
    global_: dict[str, Any] = Field(alias="global", default_factory=dict)
    roles: dict[str, dict[str, Any]] = Field(default_factory=dict)
    confirmed: bool = False

    model_config = {"populate_by_name": True}


def _effective_document() -> HostSettingsDocument | None:
    return load_host_settings()


def host_settings_view() -> dict[str, Any]:
    doc = _effective_document()
    role_names = sorted(load_roles())
    resolved = resolve_all_roles(role_names, doc)
    payload = doc.to_dict() if doc else HostSettingsDocument.empty().to_dict()
    return {
        "settings": payload,
        "resolved": [r.to_dict() for r in resolved],
        "path": str(host_settings_path()),
    }


@router.get("/host-settings")
async def get_host_settings(_peer: Principal = Depends(require_scope("staff.read"))) -> dict[str, Any]:
    return host_settings_view()


@router.put("/host-settings")
async def put_host_settings(
    body: HostSettingsPutBody,
    caller: Principal = Depends(require_scope("staff.holds.write")),
) -> dict[str, Any]:
    caller_str = format_caller(caller)
    try:
        raw = {
            "schema_version": body.schema_version,
            "global": body.global_,
            "roles": body.roles,
        }
        doc = HostSettingsDocument.from_dict(raw)
        apply_host_settings_update(doc, confirmed=body.confirmed, principal=caller_str)
    except HostSettingsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log.info("staff: host settings updated by %s", caller_str)
    return host_settings_view()
