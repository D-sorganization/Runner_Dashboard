"""One function per coordination endpoint (issues #1229, #1244).

Routers pass flat, validated bodies in and get JSON-ready dicts out; every
response carries ``generated_at``. Reads never raise for upstream trouble
(``available: False`` instead); writes raise ``RMScriptError`` (→ 502),
``ClaimHeldError`` / ``NotRegisteredError`` (→ 409), ``ImpersonationError``
(→ 403) or ``MissingAgentError`` / ``UnknownAgentError`` (→ 422).

Every write first binds the caller to its agent and session (``Caller``), so
a bot can never act for another agent, before any RM script runs.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from coordination import board, claims, roster
from coordination.auth import Caller, ImpersonationError, MissingAgentError
from coordination.models import AckBody, ClaimBody, ClaimReleaseBody, MessageBody, PresenceBody, ReleaseBody
from coordination.staff_view import staff_runs

__all__ = ["ImpersonationError", "MissingAgentError", "NotRegisteredError", "UnknownAgentError"]


class UnknownAgentError(MissingAgentError):
    """The agent is not in RM's fleet roster (RM would reject it)."""


class NotRegisteredError(RuntimeError):
    """The sending session has no live presence in the repo; RM would silently drop its message (→ 409)."""

    def __init__(self, session: str, repo: str) -> None:
        super().__init__(f"session {session!r} has no live presence in {repo}")

    def to_detail(self) -> dict[str, str]:
        return {
            "error": str(self),
            "guidance": "register presence first (POST /api/coordination/presence) in the same repo, then retry",
        }


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stamp(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "generated_at": now_iso()}


def _bind(caller: Caller, session: str, explicit_agent: str | None = None, *, needs_agent: bool = False) -> str | None:
    """Authorize ``caller`` for ``session`` (and resolve the agent when the write names one)."""
    caller.check_session(session)
    if not needs_agent:
        return None
    agent = caller.agent_for(explicit_agent)
    if agent not in roster.agent_ids():
        raise UnknownAgentError(f"agent {agent!r} is not in the Repository_Management fleet roster")
    return agent


def _require_presence(session: str, repo: str) -> None:
    if not board.has_live_session(session, repo):
        raise NotRegisteredError(session, repo)


async def sessions(repo: str | None) -> dict[str, Any]:
    data = await asyncio.to_thread(board.read_sessions, repo)
    runs, warnings = await staff_runs(repo)
    return _stamp({**data, "staff_runs": runs, "warnings": [*data.get("warnings", []), *warnings]})


def inbox(session: str, repo: str) -> dict[str, Any]:
    return _stamp(board.read_inbox(session, repo))


def register_presence(body: PresenceBody, caller: Caller) -> dict[str, Any]:
    agent = _bind(caller, body.session, body.agent, needs_agent=True)
    assert agent is not None
    args = ["--agent", agent, "--issue", str(body.issue), "--branch", body.branch]
    for path in body.paths:
        args += ["--path", path]
    for key, outcome in body.goals.items():
        args += ["--goal", f"{key}={outcome}"]
    args += ["--ttl-hours", str(float(body.ttl_hours))]
    return _stamp(board.publish(body.repo, body.session, "register", *args))


def release_presence(body: ReleaseBody, caller: Caller) -> dict[str, Any]:
    _bind(caller, body.session)
    return _stamp(board.publish(body.repo, body.session, "release"))


def send_message(body: MessageBody, caller: Caller) -> dict[str, Any]:
    _bind(caller, body.session)
    _require_presence(body.session, body.repo)
    # ``--text=`` keeps a message that starts with '-' from being parsed as an option.
    return _stamp(board.publish(body.repo, body.session, "send", "--to", body.to, f"--text={body.text}"))


def ack_message(body: AckBody, caller: Caller) -> dict[str, Any]:
    _bind(caller, body.session)
    _require_presence(body.session, body.repo)
    return _stamp(board.publish(body.repo, body.session, "ack", body.message_id))


def check_claim(repo: str, issue: int) -> dict[str, Any]:
    return _stamp(claims.check(repo, issue))


def claim_issue(body: ClaimBody, caller: Caller) -> dict[str, Any]:
    agent = _bind(caller, body.session, body.agent, needs_agent=True)
    assert agent is not None
    return _stamp(claims.claim(body.repo, body.issue, agent=agent, session=body.session, intent=body.intent))


def release_claim(body: ClaimReleaseBody, caller: Caller) -> dict[str, Any]:
    agent = _bind(caller, body.session, body.agent, needs_agent=True)
    assert agent is not None
    return _stamp(claims.release(body.repo, body.issue, agent=agent, session=body.session, reason=body.reason))
