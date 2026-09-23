"""Write authentication for the coordination API (issue #1229).

A write is accepted from a principal holding ``coordination.write`` (the
``bot`` and ``operator`` presets carry it — mint one bot token per agent), or
from the node's own loopback orchestrator peer when ``DASHBOARD_LOOPBACK_AUTH=1``.
Unlike ``require_orchestrator_peer`` the shared ``HUB_FLEET_TOKEN`` is not a
write credential: every remote writer is an identifiable principal.

Impersonation (#1244): a bot principal must be named ``agent-<name>`` and may
only act as agent ``<name>``, on sessions whose id starts with ``<name>-``
(the fleet session convention, e.g. ``codex-20260923-1045``). A bot with the
``admin`` or ``operator`` role, a human operator/admin, and the loopback peer
may act as any agent on any session.

``require_writer(scope)`` is the one write dependency: coordination writes use
``coordination.write`` and operator directives use ``priorities.write`` (#1243),
which only operators and admins hold, so bots cannot steer staff prompts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from identity import (
    Principal,
    _is_loopback_request,
    _loopback_auth_enabled,
    _resolve_principal_optional,
    auth_header,
    principal_has_scope,
)

WRITE_SCOPE = "coordination.write"
PRIORITIES_WRITE_SCOPE = "priorities.write"
BOT_PREFIX = "agent-"
UNRESTRICTED_ROLES = frozenset({"admin", "operator"})


class MissingAgentError(ValueError):
    """Neither the body nor a bot principal names the agent (→ 422)."""


class ImpersonationError(PermissionError):
    """A bot tried to act as another agent or on another agent's session (→ 403)."""


@dataclass(frozen=True)
class Caller:
    """Who is writing. A ``restricted`` caller (bot) may only act as ``agent`` (None: misnamed bot, no agent)."""

    label: str
    restricted: bool = False
    agent: str | None = None

    def _own_agent(self) -> str:
        assert self.restricted, "only restricted callers are bound to one agent"
        if not self.agent:
            raise ImpersonationError(
                f"{self.label} is a bot not named {BOT_PREFIX}<agent>; mint one bot token per agent "
                f"with principal id {BOT_PREFIX}<agent> (e.g. {BOT_PREFIX}codex)"
            )
        return self.agent

    def agent_for(self, explicit: str | None) -> str:
        """The agent this write acts as. Raises ``ImpersonationError`` / ``MissingAgentError``."""
        if self.restricted:
            own = self._own_agent()
            if explicit and explicit != own:
                raise ImpersonationError(f"{self.label} may only act as agent {own!r}, not {explicit!r}")
            return own
        if not explicit:
            raise MissingAgentError("agent is required unless you authenticate with a bot principal")
        return explicit

    def check_session(self, session: str) -> None:
        """A bot may only write for sessions named ``<agent>-...``."""
        if not self.restricted:
            return
        prefix = f"{self._own_agent()}-"
        if not session.startswith(prefix):
            raise ImpersonationError(f"{self.label} may only use sessions starting with {prefix!r}")


def _caller(principal: Principal) -> Caller:
    label = f"principal:{principal.id}"
    if principal.type != "bot" or UNRESTRICTED_ROLES.intersection(principal.roles):
        return Caller(label=label)
    named = principal.id.startswith(BOT_PREFIX) and len(principal.id) > len(BOT_PREFIX)
    return Caller(label=label, restricted=True, agent=principal.id.removeprefix(BOT_PREFIX) if named else None)


def require_writer(scope: str) -> Callable[..., Caller]:
    """FastAPI dependency accepting a principal holding ``scope`` or the loopback peer.

    The dependency answers 401 without credentials and 403 for a principal lacking ``scope``;
    the shared ``HUB_FLEET_TOKEN`` is never a write credential.
    """
    assert scope and "." in scope, f"scope must look like 'area.write', got {scope!r}"

    def writer(
        request: Request,
        header_token: str | None = Depends(auth_header),  # noqa: B008
    ) -> Caller:
        principal = _resolve_principal_optional(request, header_token)
        if principal is not None:
            if principal_has_scope(principal, scope):
                return _caller(principal)
            raise HTTPException(
                status_code=403,
                detail={"error": "Authorization failed", "required_scope": scope, "principal": principal.id},
            )
        if _loopback_auth_enabled() and _is_loopback_request(request):
            return Caller(label="loopback-dev")
        raise HTTPException(status_code=401, detail="Authentication required")

    writer.__name__ = f"require_{scope.split('.', 1)[0]}_writer"
    return writer


require_coordination_writer = require_writer(WRITE_SCOPE)
require_priorities_writer = require_writer(PRIORITIES_WRITE_SCOPE)
