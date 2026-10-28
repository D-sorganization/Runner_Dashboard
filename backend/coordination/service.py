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
import concurrent.futures
import threading
from datetime import UTC, datetime
from typing import Any

from activity_producers import emit_activity
from coordination import board, claims, roster
from coordination.auth import Caller, ImpersonationError, MissingAgentError
from coordination.models import AckBody, ClaimBody, ClaimReleaseBody, MessageBody, PresenceBody, ReleaseBody
from coordination.staff_view import staff_runs
from staff.rm_sync import stale_source_warning

__all__ = ["ImpersonationError", "MissingAgentError", "NotRegisteredError", "UnknownAgentError"]

SESSION_RESPONSE_DEADLINE_SECONDS = 5.0
_SESSION_READ_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="coord-sessions")
_SESSION_READ_LOCK = threading.Lock()
_SESSION_READS: dict[str, concurrent.futures.Future[Any]] = {}


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
    """Read board and staff state in parallel, independently bounded by one response deadline."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SESSION_RESPONSE_DEADLINE_SECONDS
    board_future = _shared_session_read("board", lambda: board.read_sessions())
    staff_future = _shared_session_read("staff", lambda: asyncio.run(staff_runs()))
    pending = {"board": board_future, "staff": staff_future}
    while pending and loop.time() < deadline:
        pending = {name: future for name, future in pending.items() if not future.done()}
        if pending:
            await asyncio.sleep(min(0.01, max(0.0, deadline - loop.time())))

    warnings: list[str] = []
    if board_future.done():
        try:
            data = board_future.result()
        except Exception:  # noqa: BLE001 — independent upstream failure must not fail this endpoint
            data = _unavailable_sessions("board sessions read failed")
            warnings.append("board sessions read failed")
    else:
        data = _unavailable_sessions("board sessions read timed out")
        warnings.append("board sessions read timed out")

    if staff_future.done():
        try:
            runs, staff_warnings = staff_future.result()
        except Exception:  # noqa: BLE001 — independent upstream failure must not fail this endpoint
            runs, staff_warnings = [], ["staff runs unavailable: read failed"]
    else:
        runs, staff_warnings = [], ["staff runs read timed out"]
    if repo:
        wanted = repo.casefold()
        if data.get("available"):
            data = {
                **data,
                "sessions": [s for s in data.get("sessions", []) if str(s.get("repo") or "").casefold() == wanted],
            }
        runs = [run for run in runs if str(run.get("repo") or "").casefold() == wanted]
    source_warning = stale_source_warning()
    if source_warning:
        warnings.append(source_warning)
    return _stamp({**data, "staff_runs": runs, "warnings": [*data.get("warnings", []), *warnings, *staff_warnings]})


def _shared_session_read(key: str, read: Any) -> concurrent.futures.Future[Any]:
    """Share a pending board or staff read so timed-out callers cannot queue duplicates."""
    with _SESSION_READ_LOCK:
        future = _SESSION_READS.get(key)
        if future is not None and not future.done():
            return future
        future = _SESSION_READ_POOL.submit(read)
        _SESSION_READS[key] = future

    def finished(completed: concurrent.futures.Future[Any]) -> None:
        try:
            completed.exception()  # consume late failures even when every waiter timed out or cancelled
        except concurrent.futures.CancelledError:
            pass
        with _SESSION_READ_LOCK:
            if _SESSION_READS.get(key) is completed:
                _SESSION_READS.pop(key, None)

    future.add_done_callback(finished)
    return future


def _unavailable_sessions(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "complete": False,
        "sessions": [],
        "messages": [],
        "conflicts": [],
        "warnings": [],
    }


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
    result = _stamp(board.publish(body.repo, body.session, "register", *args))
    emit_activity(
        source="coordination",
        session_id=body.session,
        node="hub",
        kind="presence_register",
        repo=body.repo,
        issue=body.issue,
        branch=body.branch,
    )
    return result


def release_presence(body: ReleaseBody, caller: Caller) -> dict[str, Any]:
    _bind(caller, body.session)
    result = _stamp(board.publish(body.repo, body.session, "release"))
    emit_activity(source="coordination", session_id=body.session, node="hub", kind="presence_release", repo=body.repo)
    return result


def send_message(body: MessageBody, caller: Caller) -> dict[str, Any]:
    _bind(caller, body.session)
    _require_presence(body.session, body.repo)
    # ``--text=`` keeps a message that starts with '-' from being parsed as an option.
    result = _stamp(board.publish(body.repo, body.session, "send", "--to", body.to, f"--text={body.text}"))
    emit_activity(
        source="coordination",
        session_id=body.session,
        node="hub",
        kind="message_sent",
        repo=body.repo,
        payload={"to": body.to, "text": body.text},
    )
    return result


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


BOARD_SESSION_KEYS = ("session", "agent", "repo", "issue", "branch", "expires")


async def board_sessions_bounded() -> tuple[list[dict[str, Any]], str | None]:
    """Live board sessions for the staff summary, bounded like ``sessions`` (#2980).

    Post: never raises; on an unavailable, failed or slow board read returns ``([], reason)``.
    Shares the in-flight board read with ``sessions`` and never cancels it.
    """
    future = _shared_session_read("board", lambda: board.read_sessions())
    try:
        data = await asyncio.wait_for(
            asyncio.shield(asyncio.wrap_future(future)), timeout=SESSION_RESPONSE_DEADLINE_SECONDS
        )
    except TimeoutError:
        return [], "board sessions read timed out"
    except Exception:  # noqa: BLE001 — the summary must not fail on a board read
        return [], "board sessions read failed"
    if not data.get("available"):
        return [], f"board unavailable: {data.get('reason') or 'unknown reason'}"
    return [{k: s.get(k) for k in BOARD_SESSION_KEYS} for s in data.get("sessions", [])], None
