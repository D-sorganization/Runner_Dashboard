"""Stdlib-only Python client for the Runner Dashboard Fleet API (#1228, #1323).

One method per endpoint of the Fleet Coordination API contract v1 (staff, coordination,
priorities). Agents of any vendor (Claude Code, Codex, Gemini CLI, Grok Bot) use it
directly, through ``fleetctl.py`` or through the ``fleet_mcp.py`` MCP server.

Design by contract: every method validates its arguments *before* any network I/O and
raises :class:`FleetArgumentError` (a ``ValueError``) on a precondition violation. Any
non-2xx response or transport failure raises :class:`FleetAPIError` carrying the HTTP
status (``0`` when the server was unreachable) and the decoded response body.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from fleet_validators import (
    BROADCAST,
    LIMITS,
    PATTERNS,
    PROPOSAL_DECISIONS,
    RUN_STATUSES,
    USAGE_GROUPS,
    FleetAPIError,
    FleetArgumentError,
    _check,
    _compact,
    _decode,
    _match,
    _non_negative_int,
    _opt,
    _opt_text,
    _positive_int,
    _resolve_session,
    _text,
    _validate_directive,
    default_session,
)

__all__ = [
    "BROADCAST",
    "DEFAULT_URL",
    "FleetAPIError",
    "FleetArgumentError",
    "FleetClient",
    "LIMITS",
    "PATTERNS",
    "PROPOSAL_DECISIONS",
    "RUN_STATUSES",
    "USAGE_GROUPS",
    "default_session",
]

DEFAULT_URL = "http://127.0.0.1:8321"
DEFAULT_TIMEOUT = 30.0
USER_AGENT = "fleet-client/1.0 (+Runner_Dashboard clients/fleet)"


class FleetClient:
    """Thin, validated wrapper over the dashboard's ``/api/staff``, ``/api/coordination``,
    ``/api/priorities``, and ``/api/v1/staff`` endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        timeout: float | None = None,
        agent: str | None = None,
        session: str | None = None,
    ) -> None:
        url = (base_url or os.environ.get("FLEET_API_URL") or DEFAULT_URL).strip().rstrip("/")
        parsed = urllib.parse.urlparse(url)
        _check(parsed.scheme in ("http", "https") and bool(parsed.netloc), f"invalid FLEET_API_URL: {url!r}")
        self.base_url = url
        self.token = token if token is not None else (os.environ.get("FLEET_API_TOKEN") or None)
        raw_timeout = timeout if timeout is not None else float(os.environ.get("FLEET_API_TIMEOUT") or DEFAULT_TIMEOUT)
        _check(raw_timeout > 0, "timeout must be > 0")
        self.timeout = float(raw_timeout)
        self.agent = _opt(
            PATTERNS.agent, agent if agent is not None else (os.environ.get("FLEET_AGENT") or None), "agent"
        )
        self._configured_session = _opt(
            PATTERNS.session, session if session is not None else (os.environ.get("FLEET_SESSION") or None), "session"
        )
        self.session = self._session(None) if (self._configured_session or self.agent) else None

    # ------------------------------------------------------------------ transport

    def _headers(self, has_body: bool) -> dict[str, str]:
        headers = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "User-Agent": USER_AGENT}
        if has_body:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Send request and return decoded JSON. Network errors retried for idempotent calls only."""
        _check(path.startswith("/api/"), f"path must start with /api/: {path!r}")
        query = urllib.parse.urlencode(
            {
                k: ("true" if v is True else "false" if v is False else v)
                for k, v in (params or {}).items()
                if v is not None
            }
        )
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        data = None if body is None else json.dumps(body).encode("utf-8")
        req_headers = self._headers(data is not None)
        if headers:
            req_headers.update(headers)

        call_timeout = timeout if timeout is not None else self.timeout
        is_idempotent = method in ("GET", "HEAD", "PUT", "DELETE") or "Idempotency-Key" in req_headers
        max_attempts = 2 if is_idempotent else 1

        for attempt in range(max_attempts):
            req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
            try:
                with urllib.request.urlopen(req, timeout=call_timeout) as resp:
                    return _decode(resp.read())
            except urllib.error.HTTPError as exc:
                body_decoded = _decode(exc.read())
                if is_idempotent and exc.code in (502, 503, 504) and attempt < max_attempts - 1:
                    time.sleep(0.2)
                    continue
                raise FleetAPIError(exc.code, body_decoded) from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if is_idempotent and attempt < max_attempts - 1:
                    time.sleep(0.2)
                    continue
                reason = getattr(exc, "reason", exc)
                raise FleetAPIError(0, {"error": "unreachable", "url": self.base_url, "reason": str(reason)}) from None

    def _session(self, session: str | None, agent: str | None = None) -> str:
        return _resolve_session(self._configured_session, session, agent or self.agent)

    def _agent(self, agent: str | None) -> str | None:
        return _opt(PATTERNS.agent, agent if agent is not None else self.agent, "agent")

    # ------------------------------------------------------------------ staff & conversations

    def staff_summary(self) -> Any:
        return self.request("GET", "/api/staff/summary")

    def staff_roster(self) -> Any:
        return self.request("GET", "/api/staff/roster")

    def staff_board(self, local: bool = False) -> Any:
        return self.request("GET", "/api/staff/board", params={"local": True} if local else None)

    def staff_runs(
        self, limit: int = 50, role: str | None = None, status: str | None = None, since: str | None = None
    ) -> Any:
        _check(status is None or status in RUN_STATUSES, f"status must be one of {RUN_STATUSES}")
        params = {
            "limit": _positive_int(limit, "limit"),
            "role": _opt(PATTERNS.role, role, "role"),
            "status": status,
            "since": _opt(PATTERNS.iso, since, "since"),
        }
        return self.request("GET", "/api/staff/runs", params=params)

    def run(self, run_id: str, events: int = 200) -> Any:
        return self.request(
            "GET",
            f"/api/staff/runs/{_match(PATTERNS.ident, run_id, 'run_id')}",
            params={"events": _positive_int(events, "events")},
        )

    def staff_schedule(self) -> Any:
        return self.request("GET", "/api/staff/schedule")

    def holds(self) -> Any:
        return self.request("GET", "/api/staff/holds")

    def usage(self, since: str | None = None, group: str | None = None) -> Any:
        _check(group is None or group in USAGE_GROUPS, f"group must be one of {USAGE_GROUPS}")
        params = {"since": _opt(PATTERNS.date, since, "since"), "group": group}
        return self.request("GET", "/api/staff/usage", params=params)

    def dispatch(
        self,
        role: str,
        repo: str | None = None,
        issue: int | None = None,
        pr: int | None = None,
        prompt: str | None = None,
        provider: str | None = None,
        machine: str = "auto",
        dry_run: bool = False,
        model: str | None = None,
    ) -> Any:
        role = _match(PATTERNS.role, role, "role")
        _check(issue is not None or pr is not None or bool(prompt), "one of issue, pr or prompt is required")
        body = {
            "repo": _opt(PATTERNS.bare_repo, repo, "repo (bare name, no owner)"),
            "issue": None if issue is None else _positive_int(issue, "issue"),
            "pr": None if pr is None else _positive_int(pr, "pr"),
            "prompt": None if prompt is None else _text(prompt, "prompt", LIMITS.max_prompt, required=False),
            "provider": _opt(PATTERNS.role, provider, "provider"),
            "model": _opt(PATTERNS.ident, model, "model"),
            "machine": _match(PATTERNS.ident, machine, "machine"),
            "dry_run": bool(dry_run),
        }
        return self.request("POST", f"/api/staff/{role}/run", body=_compact(body))

    def cancel(self, run_id: str) -> Any:
        rid = _match(PATTERNS.ident, run_id, "run_id")
        return self.request("POST", f"/api/staff/runs/{urllib.parse.quote(rid, safe='')}/cancel", body={})

    def staff_run_cancel(self, run_id: str) -> Any:
        return self.cancel(run_id)

    def staff_threads_list(
        self,
        participant: str | None = None,
        status: str | None = None,
        unread_by: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Any:
        params = {
            "role": _opt(PATTERNS.ident, participant, "participant"),
            "status": _opt_text(status, "status", 32),
            "unread": True if unread_by else None,
            "cursor": _opt_text(cursor, "cursor", 128),
            "limit": _positive_int(limit, "limit"),
        }
        return self.request("GET", "/api/v1/staff/threads", params=params)

    def staff_thread_open(
        self,
        role: str = "auto",
        title: str | None = None,
        initial_message: str | None = None,
        kind: str = "direct",
        project_id: str | None = None,
    ) -> Any:
        body = {
            "role": _opt(PATTERNS.ident, role, "role"),
            "title": _opt_text(title, "title", 200),
            "kind": _opt_text(kind, "kind", 32) or "direct",
            "project_id": _opt_text(project_id, "project_id", 100),
        }
        thread = self.request("POST", "/api/v1/staff/threads", body=_compact(body))
        if initial_message and isinstance(thread, dict) and "id" in thread:
            self.staff_message_send(thread["id"], initial_message)
        return thread

    def staff_message_send(self, thread_id: str, body: str, idempotency_key: str | None = None) -> Any:
        tid = _match(PATTERNS.ident, thread_id, "thread_id")
        text = _text(body, "body", LIMITS.max_prompt)
        key = idempotency_key or f"msg_key_{uuid.uuid4().hex[:12]}"
        headers = {"Idempotency-Key": key}
        return self.request(
            "POST", f"/api/v1/staff/threads/{tid}/messages", body={"body": text, "kind": "text"}, headers=headers
        )

    def staff_thread_read(self, thread_id: str, since_seq: int = 0, limit: int = 100) -> Any:
        tid = _match(PATTERNS.ident, thread_id, "thread_id")
        params = {
            "since_seq": _non_negative_int(since_seq, "since_seq"),
            "limit": _positive_int(limit, "limit"),
        }
        return self.request("GET", f"/api/v1/staff/threads/{tid}", params=params)

    def staff_thread_wait(self, thread_id: str, since_seq: int = 0, timeout: float = 60.0) -> Any:
        """Long-poll up to timeout seconds (max 60) for a reply on thread_id."""
        tid = _match(PATTERNS.ident, thread_id, "thread_id")
        wait_timeout = min(max(1.0, float(timeout)), 60.0)
        deadline = time.time() + wait_timeout
        while True:
            res = self.staff_thread_read(tid, since_seq=since_seq)
            messages = res.get("messages") or [] if isinstance(res, dict) else []
            replies = [
                m for m in messages if m.get("seq", 0) > since_seq and not str(m.get("id", "")).startswith("pending_")
            ]
            if replies:
                return {"thread_id": tid, "messages": replies, "timeout": False}
            if time.time() >= deadline:
                return {"thread_id": tid, "messages": [], "timeout": True}
            time.sleep(0.5)

    def staff_work_items(
        self,
        mine: bool | None = None,
        overdue: bool | None = None,
        waiting_on_me: bool | None = None,
        state: str | None = None,
        thread_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Any:
        params = {
            "mine": True if mine else None,
            "overdue": True if overdue else None,
            "waiting_on_me": True if waiting_on_me else None,
            "state": _opt_text(state, "state", 32),
            "thread_id": _opt(PATTERNS.ident, thread_id, "thread_id"),
            "cursor": _opt_text(cursor, "cursor", 128),
            "limit": _positive_int(limit, "limit"),
        }
        return self.request("GET", "/api/v1/staff/work-items", params=params)

    def staff_approvals_list(
        self, thread_id: str | None = None, state: str | None = "proposed", limit: int = 50
    ) -> Any:
        params = {
            "thread_id": _opt(PATTERNS.ident, thread_id, "thread_id"),
            "state": _opt_text(state, "state", 32),
            "limit": _positive_int(limit, "limit"),
        }
        return self.request("GET", "/api/v1/staff/proposals", params=params)

    def staff_approval_decide(self, proposal_id: str, decision: str, reason: str | None = None) -> Any:
        pid = _match(PATTERNS.ident, proposal_id, "proposal_id")
        _check(decision in PROPOSAL_DECISIONS, f"decision must be one of {PROPOSAL_DECISIONS}")
        body = {"decision": decision, "reason": _opt_text(reason, "reason", LIMITS.max_reason) or ""}
        return self.request("POST", f"/api/v1/staff/proposals/{pid}/decide", body=body)

    # ------------------------------------------------------------------ coordination

    def sessions(self, repo: str | None = None) -> Any:
        return self.request("GET", "/api/coordination/sessions", params={"repo": _opt(PATTERNS.repo, repo, "repo")})

    def inbox(self, session: str | None = None) -> Any:
        return self.request("GET", "/api/coordination/inbox", params={"session": self._session(session)})

    def register_presence(
        self,
        repo: str,
        issue: int,
        branch: str,
        session: str | None = None,
        agent: str | None = None,
        paths: list[str] | None = None,
        goals: dict[str, Any] | None = None,
        ttl_hours: float | None = None,
    ) -> Any:
        if paths is not None:
            limit = LIMITS.max_paths
            _check(isinstance(paths, list) and len(paths) <= limit, f"paths must be a list of <= {limit}")
            paths = [_text(p, "paths[]", 300) for p in paths]
        _check(goals is None or isinstance(goals, dict), "goals must be an object")
        _check(
            ttl_hours is None or (isinstance(ttl_hours, (int, float)) and 0 < ttl_hours <= 8),
            "ttl_hours must be in (0, 8]",
        )
        body = {
            "agent": self._agent(agent),
            "session": self._session(session, agent),
            "repo": _match(PATTERNS.repo, repo, "repo"),
            "issue": _positive_int(issue, "issue"),
            "branch": _match(PATTERNS.branch, branch, "branch"),
            "paths": paths,
            "goals": goals,
            "ttl_hours": ttl_hours,
        }
        return self.request("POST", "/api/coordination/presence", body=_compact(body))

    def release_presence(self, repo: str, session: str | None = None) -> Any:
        body = {"session": self._session(session), "repo": _match(PATTERNS.repo, repo, "repo")}
        return self.request("POST", "/api/coordination/presence/release", body=body)

    def send_message(self, repo: str, to: str, text: str, session: str | None = None) -> Any:
        """Message one session id, or ``*`` for every session registered in ``repo``."""
        recipient = to if to == BROADCAST else _match(PATTERNS.session, to, "to (session id or '*')")
        body = {
            "session": self._session(session),
            "repo": _match(PATTERNS.repo, repo, "repo"),
            "to": recipient,
            "text": _text(text, "text", form="message"),
        }
        return self.request("POST", "/api/coordination/messages", body=body)

    def ack(self, repo: str, message_id: str, session: str | None = None) -> Any:
        body = {
            "session": self._session(session),
            "repo": _match(PATTERNS.repo, repo, "repo"),
            "message_id": _match(PATTERNS.message_id, message_id, "message_id"),
        }
        return self.request("POST", "/api/coordination/messages/ack", body=body)

    def ack_message(self, repo: str, message_id: str, session: str | None = None) -> Any:
        return self.ack(repo, message_id, session=session)

    def check_claim(self, repo: str, issue: int) -> Any:
        params = {"repo": _match(PATTERNS.repo, repo, "repo"), "issue": _positive_int(issue, "issue")}
        return self.request("GET", "/api/coordination/claims", params=params)

    def claim(
        self, repo: str, issue: int, intent: str | None = None, agent: str | None = None, session: str | None = None
    ) -> Any:
        """Lease an issue. Raises FleetAPIError(409) when another agent holds it. No intent = server default."""
        intent_text = _opt_text(intent, "intent", LIMITS.max_intent)
        _check(intent_text is None or not intent_text.startswith("-"), "intent must not start with '-'")
        body = {
            "repo": _match(PATTERNS.repo, repo, "repo"),
            "issue": _positive_int(issue, "issue"),
            "agent": self._agent(agent),
            "session": self._session(session, agent),
            "intent": intent_text,
        }
        return self.request("POST", "/api/coordination/claims", body=_compact(body))

    def release_claim(
        self, repo: str, issue: int, reason: str | None = None, agent: str | None = None, session: str | None = None
    ) -> Any:
        """Release this agent's lease. No reason = server default."""
        body = {
            "repo": _match(PATTERNS.repo, repo, "repo"),
            "issue": _positive_int(issue, "issue"),
            "agent": self._agent(agent),
            "session": self._session(session, agent),
            "reason": _opt_text(reason, "reason", LIMITS.max_reason),
        }
        return self.request("POST", "/api/coordination/claims/release", body=_compact(body))

    def briefing(self, repo: str | None = None, agent: str | None = None) -> Any:
        params = {"repo": _opt(PATTERNS.repo, repo, "repo"), "agent": self._agent(agent)}
        return self.request("GET", "/api/coordination/briefing", params=params)

    # ------------------------------------------------------------------ priorities

    def priorities(self) -> Any:
        return self.request("GET", "/api/priorities")

    def meetings(self) -> Any:
        return self.request("GET", "/api/priorities/meetings")

    def meeting(self, date: str) -> Any:
        return self.request("GET", f"/api/priorities/meetings/{_match(PATTERNS.date, date, 'date (YYYY-MM-DD)')}")

    def directives(self) -> Any:
        return self.request("GET", "/api/priorities/directives")

    def set_directives(self, directives: list[dict[str, Any]], version: str | None = None) -> Any:
        """Replace the operator directive list (``priorities.write``). Each item: text, priority 1-5, repo|"*".

        Pass the ``version`` from :meth:`directives` to get a 409 instead of overwriting a concurrent edit.
        """
        _check(isinstance(directives, list), "directives must be a list")
        _check(len(directives) <= LIMITS.max_directives, f"at most {LIMITS.max_directives} directives")
        items = [_validate_directive(item, i) for i, item in enumerate(directives)]
        body: dict[str, Any] = {"directives": items}
        if version is not None:
            body["version"] = _text(version, "version", LIMITS.max_version, form="line")
        return self.request("PUT", "/api/priorities/directives", body=body)
