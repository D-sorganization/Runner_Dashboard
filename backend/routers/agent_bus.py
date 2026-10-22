"""Agent principal registry and inbox API (epic #2155, slice 1)."""

from __future__ import annotations

import hmac
import logging
import os
from typing import Annotated, Any

from agent_bus.models import AckBody, HeartbeatBody, RegisterBody, SendBody
from agent_bus.store import AgentBusStore, get_store
from coordination.auth import WRITE_SCOPE, _is_loopback_request, _reject_unauthenticated_bearer
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from identity import Principal, _loopback_auth_enabled, _resolve_principal_optional, auth_header, principal_has_scope
from staff.rate_limit import check_rate_limit

log = logging.getLogger("dashboard.agent_bus")
router = APIRouter(prefix="/api/v1/agent", tags=["agent-bus"])


def require_agent_bus_principal(
    request: Request,
    header_token: str | None = Depends(auth_header),
) -> Principal:
    """``coordination.write`` without RM session impersonation rules (fleet-session principals)."""
    principal = _resolve_principal_optional(request, header_token)
    if principal is not None:
        if principal_has_scope(principal, WRITE_SCOPE):
            return principal
        raise HTTPException(
            status_code=403,
            detail={"error": "Authorization failed", "required_scope": WRITE_SCOPE, "principal": principal.id},
        )
    _reject_unauthenticated_bearer(header_token)
    if _loopback_auth_enabled() and _is_loopback_request(request):
        return Principal(id="__loopback__", type="bot", name="loopback", roles=["loopback"])
    raise HTTPException(status_code=401, detail="Authentication required")


Writer = Annotated[Principal, Depends(require_agent_bus_principal)]


def _store() -> AgentBusStore:
    return get_store()


def _fleet_or_writer(
    request: Request,
    header_token: str | None = Depends(auth_header),
) -> tuple[Principal, bool]:
    """Authenticate register; return whether to mint a session-scoped bearer."""
    prin = _resolve_principal_optional(request, header_token)
    if prin is not None and principal_has_scope(prin, "coordination.write"):
        mint = not (prin.type == "bot" and prin.id.startswith("agent-"))
        return prin, mint
    hub_token = os.environ.get("HUB_FLEET_TOKEN", "")
    if hub_token and header_token and header_token.startswith("Bearer "):
        presented = header_token[len("Bearer ") :]
        if hmac.compare_digest(presented, hub_token):
            return Principal(id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"]), True
    raise HTTPException(status_code=401, detail="Authentication required")


def _assert_session_owner(session: str, principal: Principal, row_principal_id: str) -> None:
    if principal.id.startswith("agent-") and principal.type == "bot":
        agent = principal.id.removeprefix("agent-")
        if not session.startswith(f"{agent}-") and session != row_principal_id:
            raise HTTPException(status_code=403, detail={"error": "session id must match agent prefix"})
    if principal.id not in (row_principal_id, "fleet-peer") and "admin" not in principal.roles:
        if principal.id != row_principal_id:
            raise HTTPException(status_code=403, detail={"error": "session not owned by this principal"})


@router.post("/register")
async def post_register(
    body: RegisterBody,
    auth: tuple[Principal, bool] = Depends(_fleet_or_writer),
) -> dict[str, Any]:
    principal, mint = auth
    if principal.type == "bot" and principal.id.startswith("agent-") and not mint:
        agent = principal.id.removeprefix("agent-")
        if body.role != agent and not body.session.startswith(f"{agent}-"):
            raise HTTPException(status_code=403, detail={"error": "session must match registering agent"})
    return _store().register(
        session_id=body.session,
        role=body.role,
        provider=body.provider,
        node=body.node,
        principal=principal,
        lease_seconds=body.lease_seconds,
        mint_token=mint,
    )


@router.post("/heartbeat")
async def post_heartbeat(body: HeartbeatBody, principal: Writer) -> dict[str, Any]:
    row = _store().get_principal(body.session)
    if row is None:
        raise HTTPException(status_code=404, detail="session not registered")
    _assert_session_owner(body.session, principal, row.principal_id)
    try:
        return _store().heartbeat(body.session, body.lease_seconds)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/inbox")
async def get_inbox(
    principal: Writer,
    session: str = Query(min_length=1),
    since_cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    row = _store().get_principal(session)
    if row is None:
        raise HTTPException(status_code=404, detail="session not registered")
    _assert_session_owner(session, principal, row.principal_id)
    try:
        return _store().list_inbox(session, since_cursor, limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/messages")
async def post_message(body: SendBody, principal: Writer) -> dict[str, Any]:
    row = _store().get_principal(body.session)
    if row is None:
        raise HTTPException(status_code=404, detail="session not registered")
    _assert_session_owner(body.session, principal, row.principal_id)
    check_rate_limit("agent_bus.send", principal)
    try:
        return _store().send(body)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/messages/ack")
async def post_ack(body: AckBody, principal: Writer) -> dict[str, Any]:
    row = _store().get_principal(body.session)
    if row is None:
        raise HTTPException(status_code=404, detail="session not registered")
    _assert_session_owner(body.session, principal, row.principal_id)
    try:
        return _store().ack(body.session, body.message_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="message not found for this recipient") from exc
