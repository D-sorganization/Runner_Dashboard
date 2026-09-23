"""Issue claims (leases) via the RM lease scripts (issues #1229, #1244).

Leases stay as issue comments plus ``claim:*`` labels written by RM
``post_agent_lease`` / ``release_agent_lease``; ``check_agent_claim`` is the
read. Nothing is stored here.

RM shapes this module relies on (``shared_scripts/agent_coordination.py``):

* ``check_agent_claim`` always exits 0 and fails open: ``{held: false,
  reason: "error:<Exc>"}`` means GitHub could not be read, not "free".
  ``held: true`` may carry no ``agent`` (``do-not-automate`` label, open PR,
  referencing branch) and never carries the lease's ``session`` today.
* ``post_agent_lease`` / ``release_agent_lease`` print a ``LeaseWriteResult``:
  ``{label_updated, comment_posted, receipt, errors: [...], ok}``. The lease
  comment is the durable record, so a posted comment with a failed label is a
  partial success (200 + ``warnings``), not a failure.
"""

from __future__ import annotations

import threading
from typing import Any

from coordination.board import RMScriptError
from coordination.rm_scripts import ScriptResult, run_module

FAIL_OPEN_PREFIX = "error:"

_locks_guard = threading.Lock()
_issue_locks: dict[str, threading.Lock] = {}


class ClaimHeldError(RuntimeError):
    """The issue is held by someone other than this agent+session; the API maps this to 409."""

    def __init__(self, status: dict[str, Any]) -> None:
        super().__init__(f"claim held by {status.get('agent') or 'another holder'}")
        self.status = status

    def to_detail(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "held_by": self.status.get("agent") or "",
            "reason": self.status.get("reason") or "",
            "expires_at": self.status.get("expires_at"),
            "guidance": "Pick another issue, or message the holder via POST /api/coordination/messages. "
            "Only the same agent and session may renew a lease.",
        }


def reset_locks() -> None:
    """Forget per-issue locks (tests)."""
    with _locks_guard:
        _issue_locks.clear()


def _issue_lock(repo: str, issue: int) -> threading.Lock:
    key = f"{repo.casefold()}#{issue}"
    with _locks_guard:
        return _issue_locks.setdefault(key, threading.Lock())


def check(repo: str, issue: int) -> dict[str, Any]:
    """``{available, held, agent, session, reason, expires_at}``; unavailable when RM cannot answer."""
    res = run_module("check_agent_claim", "--repo", repo, "--issue", str(issue))
    data = res.json()
    if data is None or "held" not in data:
        return {"available": False, "reason": res.failure(), "held": None}
    reason = str(data.get("reason") or "")
    if reason.startswith(FAIL_OPEN_PREFIX):
        return {"available": False, "reason": f"check_agent_claim failed open ({reason})", "held": None}
    return {
        "available": True,
        "held": bool(data.get("held")),
        "agent": data.get("agent") or "",
        "session": data.get("session") or "",
        "reason": reason,
        "expires_at": data.get("expires_at") or None,
    }


def _is_own(status: dict[str, Any], agent: str, session: str) -> bool:
    """Renewal needs the same agent AND session; an unknown holder or session counts as someone else."""
    holder, holder_session = status.get("agent") or "", status.get("session") or ""
    return bool(holder and holder_session) and (holder, holder_session) == (agent, session)


def _write_result(script: str, res: ScriptResult) -> dict[str, Any]:
    """Map a ``LeaseWriteResult``: ok → result; comment posted but errors → result + warnings; else raise."""
    data = res.json()
    if data is None:
        raise RMScriptError(f"{script} failed: {res.failure()}")
    errors = [str(e) for e in data.get("errors") or []]
    if data.get("ok"):
        return {"result": data, "warnings": errors}
    if data.get("comment_posted"):
        return {"result": data, "warnings": errors or [f"{script}: partial outcome"]}
    raise RMScriptError(f"{script} failed: {'; '.join(errors) or res.failure()}")


def _post(repo: str, issue: int, agent: str, session: str, intent: str) -> dict[str, Any]:
    args = ("--repo", repo, "--issue", str(issue), "--agent", agent, "--session", session, "--intent", intent)
    return _write_result("post_agent_lease", run_module("post_agent_lease", *args))


def claim(repo: str, issue: int, *, agent: str, session: str, intent: str) -> dict[str, Any]:
    """Check, then lease, serialized per ``repo#issue`` in this process.

    Raises ``ClaimHeldError`` (held by anyone but this agent+session) or ``RMScriptError``.
    """
    with _issue_lock(repo, issue):
        status = check(repo, issue)
        if not status["available"]:
            raise RMScriptError(f"check_agent_claim unavailable: {status['reason']}")
        if status["held"] and not _is_own(status, agent, session):
            raise ClaimHeldError(status)
        posted = _post(repo, issue, agent, session, intent)
    return {"claimed": True, "previous": status, "lease": posted["result"], "warnings": posted["warnings"]}


def release(repo: str, issue: int, *, agent: str, session: str, reason: str) -> dict[str, Any]:
    args = ("--repo", repo, "--issue", str(issue), "--agent", agent, "--session", session, "--reason", reason)
    with _issue_lock(repo, issue):
        done = _write_result("release_agent_lease", run_module("release_agent_lease", *args))
    return {"released": True, "result": done["result"], "warnings": done["warnings"]}
