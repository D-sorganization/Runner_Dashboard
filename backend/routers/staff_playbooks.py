"""GET/POST playbooks: list, render pinned+resolved, assign to an eligible lane (issue #3488)."""

# ruff: noqa: B008
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from identity import require_fleet_peer
from staff.playbooks.assignments import PlaybookUnauthorizedError, create_assignment
from staff.playbooks.schema import PlaybookDefinition, PlaybookValidationError
from staff.playbooks.store import load_playbooks

router = APIRouter(prefix="/api/v1/staff/playbooks", tags=["staff-playbooks"])


def _etag(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return f'"{digest[:32]}"'


def _render(pb: PlaybookDefinition) -> dict[str, Any]:
    body = asdict(pb)
    body["parameters"] = [asdict(p) for p in pb.parameters]
    return body


def _get_playbook_or_404(playbook_id: str) -> PlaybookDefinition:
    pb = load_playbooks().get(playbook_id)
    if pb is None:
        raise HTTPException(status_code=404, detail=f"playbook {playbook_id!r} not found")
    return pb


@router.get("")
async def list_playbooks(_peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    playbooks = sorted(load_playbooks().values(), key=lambda pb: pb.playbook_id)
    return {
        "playbooks": [
            {"playbook_id": pb.playbook_id, "version": pb.version, "title": pb.title, "pin": pb.pin} for pb in playbooks
        ]
    }


@router.get("/{playbook_id}")
async def render_playbook(
    playbook_id: str, request: Request, response: Response, _peer: str = Depends(require_fleet_peer)
) -> Any:
    pb = _get_playbook_or_404(playbook_id)
    rendered = _render(pb)
    etag = _etag(rendered)
    response.headers["ETag"] = etag
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return rendered


@router.post("/{playbook_id}/assign")
async def assign_playbook(
    playbook_id: str, payload: dict[str, Any] = Body(...), _peer: str = Depends(require_fleet_peer)
) -> dict[str, Any]:
    pb = _get_playbook_or_404(playbook_id)
    role = payload.get("role")
    if not isinstance(role, str) or not role.strip():
        raise HTTPException(status_code=400, detail="role is required")
    parameters = payload.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise HTTPException(status_code=400, detail="parameters must be an object")
    try:
        return create_assignment(pb, role, parameters)
    except PlaybookUnauthorizedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlaybookValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
