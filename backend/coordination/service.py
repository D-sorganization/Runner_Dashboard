"""One function per coordination endpoint (issue #1229).

Routers pass flat, validated bodies in and get JSON-ready dicts out; every
response carries ``generated_at``. Reads never raise for upstream trouble
(``available: False`` instead); writes raise ``RMScriptError`` (→ 502),
``ClaimHeldError`` (→ 409) or ``MissingAgentError`` (→ 422).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from coordination import board, claims
from coordination.auth import Caller
from coordination.models import AckBody, ClaimBody, ClaimReleaseBody, MessageBody, PresenceBody, ReleaseBody
from coordination.staff_view import staff_runs


class MissingAgentError(ValueError):
    """Neither the body nor a bot principal names the agent."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stamp(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "generated_at": now_iso()}


def _agent(explicit: str | None, caller: Caller) -> str:
    agent = explicit or caller.default_agent
    if not agent:
        raise MissingAgentError("agent is required unless you authenticate with a bot principal")
    return agent


async def sessions(repo: str | None) -> dict[str, Any]:
    data = await asyncio.to_thread(board.read_sessions, repo)
    runs, warnings = await staff_runs(repo)
    return _stamp({**data, "staff_runs": runs, "warnings": [*data.get("warnings", []), *warnings]})


def inbox(session: str, repo: str) -> dict[str, Any]:
    return _stamp(board.read_inbox(session, repo))


def register_presence(body: PresenceBody, caller: Caller) -> dict[str, Any]:
    args = ["--agent", _agent(body.agent, caller), "--issue", str(body.issue), "--branch", body.branch]
    for path in body.paths:
        args += ["--path", path]
    for key, outcome in body.goals.items():
        args += ["--goal", f"{key}={outcome}"]
    args += ["--ttl-hours", str(float(body.ttl_hours))]
    return _stamp(board.publish(body.repo, body.session, "register", *args))


def release_presence(body: ReleaseBody) -> dict[str, Any]:
    return _stamp(board.publish(body.repo, body.session, "release"))


def send_message(body: MessageBody) -> dict[str, Any]:
    # ``--text=`` keeps a message that starts with '-' from being parsed as an option.
    return _stamp(board.publish(body.repo, body.session, "send", "--to", body.to, f"--text={body.text}"))


def ack_message(body: AckBody) -> dict[str, Any]:
    return _stamp(board.publish(body.repo, body.session, "ack", body.message_id))


def check_claim(repo: str, issue: int) -> dict[str, Any]:
    return _stamp(claims.check(repo, issue))


def claim_issue(body: ClaimBody, caller: Caller) -> dict[str, Any]:
    agent = _agent(body.agent, caller)
    return _stamp(claims.claim(body.repo, body.issue, agent=agent, session=body.session, intent=body.intent))


def release_claim(body: ClaimReleaseBody, caller: Caller) -> dict[str, Any]:
    agent = _agent(body.agent, caller)
    return _stamp(claims.release(body.repo, body.issue, agent=agent, session=body.session, reason=body.reason))
