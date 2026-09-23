"""Issue claims (leases) via the RM lease scripts (issue #1229).

Leases stay as issue comments plus ``claim:*`` labels written by RM
``post_agent_lease`` / ``release_agent_lease``; ``check_agent_claim`` is the
read. Nothing is stored here.
"""

from __future__ import annotations

from typing import Any

from coordination.board import RMScriptError
from coordination.rm_scripts import run_module


class ClaimHeldError(RuntimeError):
    """The issue is leased by a different agent; the API maps this to 409."""

    def __init__(self, status: dict[str, Any]) -> None:
        super().__init__(f"claim held by {status.get('agent') or 'another agent'}")
        self.status = status

    def to_detail(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "held_by": self.status.get("agent") or "",
            "reason": self.status.get("reason") or "",
            "expires_at": self.status.get("expires_at"),
            "guidance": "Pick another issue, or message the holder via POST /api/coordination/messages.",
        }


def check(repo: str, issue: int) -> dict[str, Any]:
    """``{available, held, agent, reason, expires_at}``; unavailable when the script cannot answer."""
    res = run_module("check_agent_claim", "--repo", repo, "--issue", str(issue))
    data = res.json()
    if data is None or "held" not in data:
        return {"available": False, "reason": res.failure(), "held": None}
    return {
        "available": True,
        "held": bool(data.get("held")),
        "agent": data.get("agent") or "",
        "reason": data.get("reason") or "",
        "expires_at": data.get("expires_at") or None,
    }


def claim(repo: str, issue: int, *, agent: str, session: str, intent: str) -> dict[str, Any]:
    """Check, then lease. Raises ``ClaimHeldError`` (someone else holds it) or ``RMScriptError``."""
    status = check(repo, issue)
    if not status["available"]:
        raise RMScriptError(f"check_agent_claim failed: {status['reason']}")
    if status["held"] and status["agent"] and status["agent"] != agent:
        raise ClaimHeldError(status)
    res = run_module(
        "post_agent_lease",
        *("--repo", repo, "--issue", str(issue), "--agent", agent, "--session", session, "--intent", intent),
    )
    lease = res.json()
    if lease is None or not lease.get("ok"):
        raise RMScriptError(f"post_agent_lease failed: {res.failure()}")
    return {"claimed": True, "previous": status, "lease": lease}


def release(repo: str, issue: int, *, agent: str, session: str, reason: str) -> dict[str, Any]:
    res = run_module(
        "release_agent_lease",
        *("--repo", repo, "--issue", str(issue), "--agent", agent, "--session", session, "--reason", reason),
    )
    data = res.json()
    if data is None or not data.get("ok"):
        raise RMScriptError(f"release_agent_lease failed: {res.failure()}")
    return {"released": True, "result": data}
