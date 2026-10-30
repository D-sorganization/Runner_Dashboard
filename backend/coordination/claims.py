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

import json
import re
import subprocess  # noqa: S404
import threading
from collections.abc import Callable
from typing import Any

from activity_producers import record_job_claim_activity
from coordination import board as board_mod
from coordination.board import BOARD_COMMENT_LIMIT, BOARD_ISSUE, RMScriptError
from coordination.rm_scripts import ScriptResult, run_module
from staff.claim_guard import closing_pr_for, full_repo

FAIL_OPEN_PREFIX = "error:"
GH_TIMEOUT_SECONDS = 20
_BRANCH_FOR_ISSUE = re.compile(r"(?:^|/)(\d+)(?:-|$)")

_locks_guard = threading.Lock()
_issue_locks: dict[str, threading.Lock] = {}


def _gh_cli(path: str) -> Any:
    proc = subprocess.run(  # noqa: S603
        ["gh", "api", path],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"gh api {path} failed").strip()[:300])
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


EvidenceFetch = Callable[[str], Any]
# Tests monkeypatch this seam so a unit test never touches live GitHub.
evidence_fetch: EvidenceFetch = _gh_cli


def _landed_pr(repo_full: str, issue: int) -> dict[str, Any] | None:
    """An open or merged PR whose title/body closes ``issue`` (closed-unmerged doesn't count)."""
    pulls = evidence_fetch(f"/repos/{repo_full}/pulls?state=all&per_page=100")
    candidates = [
        pr for pr in (pulls if isinstance(pulls, list) else []) if pr.get("state") != "closed" or pr.get("merged_at")
    ]
    return closing_pr_for(candidates, issue)


def _pushed_branch(repo_full: str, issue: int) -> str | None:
    """Remote branch whose last path segment is ``<issue>-...`` (the fleet's lane-branch convention)."""
    branches = evidence_fetch(f"/repos/{repo_full}/branches?per_page=100")
    for entry in branches if isinstance(branches, list) else []:
        name = str(entry.get("name") or "") if isinstance(entry, dict) else ""
        match = _BRANCH_FOR_ISSUE.search(name)
        if match and int(match.group(1)) == issue:
            return name
    return None


def classify_release_reason(repo: str, issue: int, *, agent_reason: str) -> str:
    """The real release outcome from evidence (#3274), never the agent's say-so.

    A lease release is classified from an open/merged PR or a pushed branch referencing
    ``issue``, not from ``agent_reason`` (which defaults to the misleading "work completed"
    at the request layer even when nothing landed). No evidence, or evidence unreadable,
    both fail closed to ``abandoned`` — never ``completed``. Reuses ``staff.claim_guard``'s
    PR/branch lookups (the same evidence #3035's release-outcome work relies on) rather than
    adding a second classifier.
    """
    repo_full = full_repo(repo)
    try:
        pr = _landed_pr(repo_full, issue)
        if pr is not None:
            return f"completed: PR #{pr.get('number')} {pr.get('html_url') or ''}".strip()
        branch = _pushed_branch(repo_full, issue)
        if branch is not None:
            return f"completed: branch {branch} pushed"
    except Exception as exc:  # noqa: BLE001
        return f"abandoned: evidence lookup unavailable ({exc}); agent reported: {agent_reason}"
    return f"abandoned: no PR or branch found for #{issue}; agent reported: {agent_reason}"


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


def _check_incomplete(data: dict[str, Any]) -> str | None:
    if data.get("complete") is False or data.get("truncated") or data.get("comment_limit_exceeded"):
        warnings = [str(w) for w in data.get("warnings") or []]
        return "; ".join(warnings) or "board comment limit reached; claim check incomplete"
    if data.get("page_limit_reached"):
        return "board page limit reached; claim check incomplete"
    count = data.get("comment_count")
    if isinstance(count, int) and count > BOARD_COMMENT_LIMIT and not data.get("complete", False):
        return f"comment count ({count}) exceeds {BOARD_COMMENT_LIMIT} without complete pagination"
    for warning in data.get("warnings") or []:
        text = str(warning)
        lowered = text.lower()
        if "page limit" in lowered or "comment limit" in lowered or "incomplete" in lowered:
            return text
    return None


def _board_claim_override(repo: str, issue: int) -> dict[str, Any] | None:
    state = board_mod.cached_board_state()
    if state is None and board_mod.board_page_fetcher is not None:
        state = board_mod.read_sessions(repo)
    if state is None:
        return None
    if not state.get("available"):
        return {
            "available": False,
            "reason": str(state.get("reason") or "coordination board unavailable"),
            "held": None,
        }
    for sess in state.get("sessions") or []:
        if str(sess.get("repo") or "").casefold() == repo.casefold() and sess.get("issue") == issue:
            return {
                "available": True,
                "held": True,
                "agent": str(sess.get("agent") or ""),
                "session": str(sess.get("session") or ""),
                "reason": f"active board presence on RM #{BOARD_ISSUE}",
                "expires_at": sess.get("expires") or None,
            }
    if state.get("comment_limit_exceeded") or (
        not state.get("complete", True) and board_mod._comment_limit_reached(state)
    ):
        detail = "; ".join(state.get("warnings") or [f"RM #{BOARD_ISSUE} comment limit exceeded"])
        return {"available": False, "reason": f"coordination board incomplete ({detail})", "held": None}
    return None


def check(repo: str, issue: int) -> dict[str, Any]:
    """``{available, held, agent, session, reason, expires_at}``; unavailable when RM cannot answer."""
    res = run_module("check_agent_claim", "--repo", repo, "--issue", str(issue))
    data = res.json()
    if data is None or "held" not in data:
        return {"available": False, "reason": res.failure(), "held": None}
    reason = str(data.get("reason") or "")
    if reason.startswith(FAIL_OPEN_PREFIX):
        return {"available": False, "reason": f"check_agent_claim failed open ({reason})", "held": None}
    held = bool(data.get("held"))
    if not held:
        incomplete_reason = _check_incomplete(data)
        if incomplete_reason is not None:
            return {
                "available": False,
                "reason": f"check_agent_claim incomplete ({incomplete_reason})",
                "held": None,
            }
        override = _board_claim_override(repo, issue)
        if override is not None:
            return override
    return {
        "available": True,
        "held": held,
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


def claim(
    repo: str,
    issue: int,
    *,
    agent: str,
    session: str,
    intent: str,
    holder_principal: str | None = None,
    fencing_token: str | None = None,
) -> dict[str, Any]:
    """Check, then lease, serialized per ``repo#issue`` in this process.

    Raises ``ClaimHeldError`` (held by anyone but this agent+session) or ``RMScriptError``.
    """
    with _issue_lock(repo, issue):
        status = check(repo, issue)
        if not status["available"]:
            raise RMScriptError(f"check_agent_claim unavailable: {status['reason']}")
        if status["held"] and not _is_own(status, agent, session):
            held_agent = str(status.get("agent") or "")
            held_principal = f"agent-{held_agent}" if held_agent else (holder_principal or f"agent-{agent}")
            record_job_claim_activity(
                kind="conflict",
                repo=repo,
                issue=issue,
                agent=agent,
                session=session,
                holder_principal=held_principal,
                fencing_token=fencing_token,
                extra={
                    "challenger_principal": holder_principal or f"agent-{agent}",
                    "reason": str(status.get("reason") or ""),
                },
            )
            raise ClaimHeldError(status)
        posted = _post(repo, issue, agent, session, intent)
        receipt = str(posted["result"].get("receipt") or "")
        record_job_claim_activity(
            kind="claim",
            repo=repo,
            issue=issue,
            agent=agent,
            session=session,
            holder_principal=holder_principal,
            receipt=receipt,
            fencing_token=fencing_token,
            extra={"intent": intent},
        )
    return {"claimed": True, "previous": status, "lease": posted["result"], "warnings": posted["warnings"]}


def release(
    repo: str,
    issue: int,
    *,
    agent: str,
    session: str,
    reason: str,
    holder_principal: str | None = None,
    fencing_token: str | None = None,
) -> dict[str, Any]:
    outcome_reason = classify_release_reason(repo, issue, agent_reason=reason)
    args = (
        "--repo",
        repo,
        "--issue",
        str(issue),
        "--agent",
        agent,
        "--session",
        session,
        "--reason",
        outcome_reason,
    )
    with _issue_lock(repo, issue):
        done = _write_result("release_agent_lease", run_module("release_agent_lease", *args))
        receipt = str(done["result"].get("receipt") or "")
        kind: str = "expiry" if "expired" in reason.lower() else "release"
        record_job_claim_activity(
            kind=kind,  # type: ignore[arg-type]
            repo=repo,
            issue=issue,
            agent=agent,
            session=session,
            holder_principal=holder_principal,
            receipt=receipt,
            fencing_token=fencing_token,
            extra={"reason": outcome_reason},
        )
    return {"released": True, "result": done["result"], "warnings": done["warnings"]}
