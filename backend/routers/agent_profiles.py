"""Agent Profiles API router (CR-3, Issue #1283).

CRUD endpoints for saved, named agent configuration profiles.
"""

from __future__ import annotations

import logging
from typing import Any

from agent_remediation import probe_provider_availability
from agent_remediation.provider_registry import PROVIDER_REGISTRY
from code_requests.profiles import AgentProfile, AgentProfileStore
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_fleet_peer, require_scope

log = logging.getLogger("dashboard.agent_profiles")
router = APIRouter(tags=["agent_profiles"])

_store: AgentProfileStore = AgentProfileStore()


def _get_store() -> AgentProfileStore:
    return _store


@router.get("/api/agent-profiles")
async def list_agent_profiles(_peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:  # noqa: B008
    """List all saved agent profiles and live provider availability."""
    store = _get_store()
    profiles = [p.model_dump(mode="json") for p in store.list()]

    # Collect live provider availability & login status
    availability = probe_provider_availability()
    providers: list[dict[str, Any]] = []
    for entry in PROVIDER_REGISTRY:
        avail = availability.get(entry.dashboard_id)
        is_avail = bool(avail and avail.available)
        providers.append(
            {
                "id": entry.dashboard_id,
                "label": entry.label,
                "conductor_id": entry.conductor_id,
                "available": is_avail,
                "detail": avail.detail if avail else "",
                "models": list(entry.models),
                "capabilities": list(entry.capabilities),
                "cost_per_task": entry.cost_per_task,
                "enabled": entry.enabled,
            }
        )

    return {
        "profiles": profiles,
        "providers": providers,
        "total": len(profiles),
    }


@router.get("/api/agent-profiles/{profile_id}")
async def get_agent_profile(
    profile_id: str,
    _peer: str = Depends(require_fleet_peer),  # noqa: B008
) -> dict[str, Any]:
    """Get a single agent profile by id."""
    store = _get_store()
    profile = store.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Agent profile '{profile_id}' not found")
    return profile.model_dump(mode="json")


@router.post("/api/agent-profiles")
async def create_agent_profile(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict[str, Any]:
    """Create a new agent profile."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")

    store = _get_store()
    try:
        profile = AgentProfile.model_validate(body)
        created = store.create(profile)
        log.info("Created agent profile '%s' by %s", profile.id, principal.id)
        return created.model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/api/agent-profiles/{profile_id}")
async def update_agent_profile(
    profile_id: str,
    request: Request,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict[str, Any]:
    """Update an existing agent profile."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")

    store = _get_store()
    try:
        updated = store.update(profile_id, body)
        log.info("Updated agent profile '%s' by %s", profile_id, principal.id)
        return updated.model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/api/agent-profiles/{profile_id}")
async def delete_agent_profile(
    profile_id: str,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict[str, Any]:
    """Delete an agent profile by id."""
    store = _get_store()
    try:
        store.delete(profile_id)
        log.info("Deleted agent profile '%s' by %s", profile_id, principal.id)
        return {"status": "deleted", "id": profile_id}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
