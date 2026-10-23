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
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from fleet_validators import (
    BROADCAST,
    LIMITS,
    MEMORY_ENTRY_TYPES,
    PATTERNS,
    PROPOSAL_DECISIONS,
    PROPOSAL_ESTIMATED_EFFORTS,
    PROPOSAL_URGENCIES,
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
    short_host,
)

__all__ = [
    "BROADCAST",
    "CLIENT_ENVELOPE_VERSION",
    "CheckResult",
    "DEFAULT_URL",
    "FleetAPIError",
    "FleetArgumentError",
    "FleetClient",
    "LIMITS",
    "PATTERNS",
    "PROPOSAL_DECISIONS",
    "REQUIRED_SCOPES",
    "RUN_STATUSES",
    "USAGE_GROUPS",
    "default_session",
    "run_doctor",
]

_CONTEXT_QUERY_CACHE: dict[tuple[str, str, str, str], tuple[float, dict[str, Any]]] = {}
_LIBRARY_SNIPPET_MAX_BYTES = 400


def _library_snippet(value: Any) -> str:
    """Cap library snippet bodies by UTF-8 bytes; citations are separate."""
    return str(value or "").encode("utf-8")[:_LIBRARY_SNIPPET_MAX_BYTES].decode("utf-8", errors="ignore")


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
        # BR-06 (#1800): with no explicit endpoint, read from the one Barb authority so every enrolled
        # computer sees the same threads; a node-local default would show only that node's conversations.
        url = (
            (base_url or os.environ.get("FLEET_API_URL") or os.environ.get("BARB_AUTHORITY_URL") or DEFAULT_URL)
            .strip()
            .rstrip("/")
        )
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
        self._session_source = "argument" if session is not None else "env" if self._configured_session else "derived"
        self._derived: dict[str, str] = {}
        self.session = self._session(None) if (self._configured_session or self.agent) else None

    def identity(self) -> dict[str, str | None]:
        """Who this client acts as: principal agent, host and session, each reported separately."""
        source = self._session_source
        return {"agent": self.agent, "host": short_host(), "session": self.session, "session_source": source}

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
        return _resolve_session(self._configured_session, session, agent or self.agent, self._derived)

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

    def staff_skills_list(
        self,
        role: str | None = None,
        repo: str | None = None,
        task_type: str | None = None,
    ) -> Any:
        params = {
            "role": _opt(PATTERNS.ident, role, "role"),
            "repo": _opt_text(repo, "repo", 128),
            "task_type": _opt_text(task_type, "task_type", 64),
        }
        return self.request("GET", "/api/v1/staff/skills", params=_compact(params))

    def staff_skill_get(self, name: str, version: str | None = None) -> Any:
        skill = _match(PATTERNS.ident, name, "name")
        params = {"version": _opt_text(version, "version", 32)} if version else {}
        return self.request("GET", f"/api/v1/staff/skills/{urllib.parse.quote(skill, safe='')}", params=params)

    def staff_project_create(
        self,
        title: str,
        goal: str,
        *,
        definition_of_done: str = "",
        repos: list[str] | None = None,
    ) -> Any:
        body = {
            "title": _text(title, "title", 200),
            "goal": _text(goal, "goal", 4000),
            "definition_of_done": _opt_text(definition_of_done, "definition_of_done", 4000) or "",
            "repos": repos or [],
        }
        return self.request("POST", "/api/v1/staff/projects", body=body)

    def staff_projects_list(self) -> Any:
        return self.request("GET", "/api/v1/staff/projects")

    def staff_project_decompose(self, project_id: str) -> Any:
        pid = _match(PATTERNS.ident, project_id, "project_id")
        return self.request("POST", f"/api/v1/staff/projects/{urllib.parse.quote(pid, safe='')}/decompose", body={})

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

    def staff_approval_decide(
        self, proposal_id: str, decision: str, reason: str | None = None, params_hash: str | None = None
    ) -> Any:
        """Approve or deny a proposal. Approving echoes the reviewed ``params_hash`` (#1807).

        Without an explicit ``params_hash`` the client previews the proposal (GET) and echoes the
        hash the server reports, binding the approval to the parameters as stored right now.
        """
        pid = _match(PATTERNS.ident, proposal_id, "proposal_id")
        _check(decision in PROPOSAL_DECISIONS, f"decision must be one of {PROPOSAL_DECISIONS}")
        body: dict[str, Any] = {"decision": decision, "reason": _opt_text(reason, "reason", LIMITS.max_reason) or ""}
        if decision == "approved":
            body["params_hash"] = _opt(PATTERNS.sha256_hex, params_hash, "params_hash") or self._previewed_hash(pid)
        return self.request("POST", f"/api/v1/staff/proposals/{pid}/decide", body=body)

    # ------------------------------------------------------------------ shared memory

    def memory_context_pack(self) -> Any:
        """Read the shared memory context pack for the authenticated principal."""
        return self.request("GET", "/api/v1/memory/context-pack")

    def memory_list(
        self,
        since: str | None = None,
        entry_type: str | None = None,
        tag: str | None = None,
        author: str | None = None,
        limit: int = 100,
    ) -> Any:
        """List shared memory entries with optional contract filters."""
        kind = _opt_text(entry_type, "entry_type", 32)
        if kind is not None:
            _check(kind in MEMORY_ENTRY_TYPES, "invalid entry_type")
        _check(
            isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 500,
            "limit must be between 1 and 500",
        )
        params = {
            "since": _opt_text(since, "since", 64),
            "type": kind,
            "tag": _opt_text(tag, "tag", 64),
            "author": _opt_text(author, "author", 128),
            "limit": limit,
        }
        return self.request("GET", "/api/v1/memory", params=params)

    def memory_append(
        self,
        entry_type: str,
        source_product: str,
        body: dict[str, Any],
        tags: list[str] | None = None,
        expires_at: str | None = None,
        supersedes_id: str | None = None,
    ) -> Any:
        """Append one memory entry. Writes are never automatically replayed."""
        kind = _text(entry_type, "entry_type", 32)
        _check(kind in MEMORY_ENTRY_TYPES, "invalid entry_type")
        source = _text(source_product, "source_product", 64)
        _check(isinstance(body, dict), "body must be an object")
        tag_values = [] if tags is None else tags
        _check(isinstance(tag_values, list) and len(tag_values) <= 32, "tags must be an array with at most 32 items")
        normalized_tags = [_text(value, "tag", 64) for value in tag_values]
        payload: dict[str, Any] = {
            "entry_type": kind,
            "source_product": source,
            "tags": normalized_tags,
            "body": body,
        }
        if expires_at is not None:
            payload["expires_at"] = _text(expires_at, "expires_at", 64)
        if supersedes_id is not None:
            payload["supersedes_id"] = _text(supersedes_id, "supersedes_id", 128)
        return self.request("POST", "/api/v1/memory", body=payload)

    def memory_append_session_digest(
        self,
        source_product: str,
        conversation_id: str,
        user_message_id: str,
        summary: str,
        decisions: list[str] | None = None,
        open_items: list[str] | None = None,
        reported_origin_principal: str | None = None,
    ) -> Any:
        """Append one bounded conversation digest; server-side turn keys make retry safe."""
        _check(source_product in ("codex", "dashboard"), "source_product must be codex or dashboard")
        conversation = _match(PATTERNS.memory_id, conversation_id, "conversation_id")
        message_id = _match(PATTERNS.memory_id, user_message_id, "user_message_id")
        digest = _text(summary, "summary", 1000)
        decision_values = [] if decisions is None else decisions
        open_values = [] if open_items is None else open_items
        _check(isinstance(decision_values, list) and len(decision_values) <= 3, "decisions must have at most 3 items")
        _check(isinstance(open_values, list) and len(open_values) <= 3, "open_items must have at most 3 items")
        payload: dict[str, Any] = {
            "source_product": source_product,
            "conversation_id": conversation,
            "user_message_id": message_id,
            "summary": digest,
            "decisions": [_text(value, "decision", 120) for value in decision_values],
            "open_items": [_text(value, "open_item", 120) for value in open_values],
        }
        if reported_origin_principal is not None:
            payload["reported_origin_principal"] = _text(reported_origin_principal, "reported_origin_principal", 128)
        return self.request("POST", "/api/v1/memory/session-digest", body=payload)

    def _previewed_hash(self, pid: str) -> str:
        preview = self.request("GET", f"/api/v1/staff/proposals/{pid}")
        reviewed = preview.get("params_hash") if isinstance(preview, dict) else None
        _check(isinstance(reviewed, str) and bool(reviewed), "server returned no params_hash to approve against")
        return str(reviewed)

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

    # ------------------------------------------------------------------ agent bus (#2155)

    def agent_register(
        self,
        *,
        session: str,
        role: str,
        provider: str,
        node: str,
        lease_seconds: int = 300,
    ) -> Any:
        _check(30 <= lease_seconds <= 8 * 3600, "lease_seconds must be between 30 and 28800")
        body = {
            "session": self._session(session),
            "role": _match(PATTERNS.agent, role, "role"),
            "provider": _text(provider, "provider", 64),
            "node": _text(node, "node", 128),
            "lease_seconds": lease_seconds,
        }
        return self.request("POST", "/api/v1/agent/register", body=body)

    def agent_heartbeat(self, *, session: str | None = None, lease_seconds: int = 300) -> Any:
        _check(30 <= lease_seconds <= 8 * 3600, "lease_seconds must be between 30 and 28800")
        body = {"session": self._session(session), "lease_seconds": lease_seconds}
        return self.request("POST", "/api/v1/agent/heartbeat", body=body)

    def agent_inbox(self, *, session: str | None = None, since_cursor: int = 0, limit: int = 50) -> Any:
        params = {
            "session": self._session(session),
            "since_cursor": _non_negative_int(since_cursor, "since_cursor"),
            "limit": _positive_int(limit, "limit"),
        }
        return self.request("GET", "/api/v1/agent/inbox", params=params)

    def agent_bus_send(
        self,
        *,
        to: str,
        repo: str,
        work_kind: str,
        work_number: int,
        body: str,
        msg_type: str = "status",
        session: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        _check(work_kind in ("issue", "pr"), "work_kind must be issue or pr")
        key = idempotency_key or f"{self._session(session)}-lib-{uuid.uuid4().hex[:12]}"
        payload = {
            "session": self._session(session),
            "to": _text(to, "to", 128),
            "type": msg_type,
            "repo": _match(PATTERNS.repo, repo, "repo"),
            "work_kind": work_kind,
            "work_number": _positive_int(work_number, "work_number"),
            "body": _text(body, "body", 4000),
            "idempotency_key": key,
        }
        return self.request("POST", "/api/v1/agent/messages", body=payload)

    def library_search(
        self,
        query: str,
        *,
        repo: str | None = None,
        topic: str | None = None,
        budget: int | None = None,
        k: int | None = None,
    ) -> Any:
        q = _text(query, "query", 512)
        params: dict[str, Any] = {"q": q}
        if repo:
            params["repo"] = _match(PATTERNS.repo, repo, "repo")
        if topic:
            params["topic"] = _text(topic, "topic", 128)
        if budget is not None:
            params["budget"] = _positive_int(budget, "budget")
        if k is not None:
            params["k"] = _positive_int(k, "k")
        return self.request("GET", "/api/v1/library/search", params=_compact(params))

    def library_work(self, work_id: int) -> Any:
        wid = _positive_int(work_id, "work_id")
        return self.request("GET", f"/api/v1/library/works/{wid}")

    def library_submit(self, payload: dict[str, Any]) -> Any:
        if not isinstance(payload, dict):
            raise FleetArgumentError("payload must be an object")
        return self.request("POST", "/api/v1/library/submissions", body=payload)

    # ------------------------------------------------------------------ unified query (#2223)

    def fleet_query(
        self,
        source: str,
        op: str = "search",
        query: str | None = None,
        repo: str | None = None,
        topic: str | None = None,
        k: int = 5,
        offset: int = 0,
        full: bool = False,
        cite: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Unified fleet context query tool (#2223).

        Exposes on-request context across sources ('context', 'library', 'knowledge', 'memory', 'codebase').
        Ceilings enforced: top-k <= 5, library snippets <= 400 UTF-8 bytes, citations included.
        """
        valid_sources = ("context", "library", "knowledge", "memory", "codebase")
        _check(source in valid_sources, f"source must be one of {valid_sources}")
        valid_ops = ("search", "get", "request", "fetch")
        _check(op in valid_ops, f"op must be one of {valid_ops}")
        _check(op != "fetch" or source == "library", "fetch is only supported for library")
        _check(cite is None or (source == "library" and op == "fetch"), "cite is only supported for library fetch")
        _check(
            run_id is None
            or (source == "library" and op in ("search", "fetch"))
            or (source == "codebase" and op == "search"),
            "run_id is only supported for library search/fetch or codebase search",
        )
        _check(source != "codebase" or op == "search", "codebase only supports search")
        effective_run_id = run_id
        if effective_run_id is None and source == "library" and op in ("search", "fetch"):
            effective_run_id = os.environ.get("STAFF_RUN_ID")
        effective_k = min(max(1, int(k)), 5)
        offset = max(0, int(offset))

        if source == "context":
            if op == "request":
                if not query or not query.strip():
                    raise FleetArgumentError("query is required for context request op")
                msg = f"Context request for repo '{repo or 'fleet'}': {query.strip()}"
                return {
                    "status": "requested",
                    "recipient": "librarian",
                    "detail": self.send_message(repo=repo or "fleet", to="librarian", text=msg),
                }

            cache_key = (getattr(self, "token", "") or "", repo or "", query or "", op)
            now = time.time()
            cached = _CONTEXT_QUERY_CACHE.get(cache_key)
            if cached and (now - cached[0] < 30.0):
                return cached[1]

            try:
                pack = self.request("GET", "/api/v1/memory/context-pack")
            except Exception:
                return {
                    "status": "unavailable",
                    "holds": "unavailable",
                    "directives": "unavailable",
                    "lessons": "unavailable",
                    "results": [],
                }

            directives = [d for d in pack.get("directives", []) if not d.get("barb_only") and not d.get("is_barb_only")]
            holds = pack.get("holds", [])
            digests = pack.get("session_digests", [])
            tasks = pack.get("open_tasks", [])

            results: list[dict[str, Any]] = []
            for d in digests:
                body = d.get("body", {}) if isinstance(d, dict) else {}
                summary = str(body.get("summary", ""))[:400]
                results.append(
                    {
                        "id": d.get("id", ""),
                        "type": "digest",
                        "title": f"Session Digest: {d.get('id', '')}",
                        "snippet": summary,
                        "ref": f"/api/v1/memory/{d.get('id', '')}",
                    }
                )
            for t in tasks:
                body = t.get("body", {}) if isinstance(t, dict) else {}
                title = str(body.get("title", body.get("name", "Task")))
                results.append(
                    {
                        "id": t.get("id", ""),
                        "type": "task",
                        "title": title,
                        "snippet": str(body.get("description", ""))[:400],
                        "ref": f"/api/v1/memory/{t.get('id', '')}",
                    }
                )

            paged = results[offset : offset + effective_k]
            res = {
                "status": "ok",
                "source": "context",
                "holds": holds,
                "directives": directives,
                "results": paged,
                "has_more": len(results) > (offset + effective_k),
            }
            _CONTEXT_QUERY_CACHE[cache_key] = (now, res)
            return res

        elif source == "library":
            if op == "fetch":
                if cite is None:
                    raise FleetArgumentError("cite is required for library fetch")
                citation = _text(cite, "cite", 512)
                headers = (
                    {"X-Staff-Run-ID": _match(PATTERNS.ident, effective_run_id, "run_id")}
                    if effective_run_id is not None
                    else None
                )
                data = self.request("GET", "/api/v1/library/fetch", params={"cite": citation}, headers=headers)
                if not isinstance(data, dict):
                    data = {}
                result = {
                    "id": data.get("cite_key", citation),
                    "title": data.get("citation", "Library passage"),
                    "snippet": _library_snippet(data.get("text")),
                    "ref": data.get("cite_key", citation),
                    "citation": data.get("citation"),
                    "cite_key": data.get("cite_key", citation),
                    "cite_uri": data.get("cite_uri"),
                    "file_sha256": data.get("file_sha256"),
                    "work_sha256": data.get("work_sha256"),
                    "node": data.get("node"),
                    "source_id": data.get("source_id"),
                    "work_id": data.get("work_id"),
                    "passage_id": data.get("passage_id"),
                    "page_start": data.get("page_start"),
                    "page_end": data.get("page_end"),
                }
                return {"status": "ok", "source": "library", "results": [result], "has_more": False}

            if not query or not query.strip():
                raise FleetArgumentError("query is required for library search")
            params: dict[str, Any] = {"k": min(100, effective_k + offset), "q": query}
            if repo:
                params["repo"] = repo
            if topic:
                params["topic"] = topic
            headers = (
                {"X-Staff-Run-ID": _match(PATTERNS.ident, effective_run_id, "run_id")}
                if effective_run_id is not None
                else None
            )
            data = self.request("GET", "/api/v1/library/search", params=params, headers=headers)
            items = data.get("passages", []) if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = []
            paged_items = items[offset : offset + effective_k]
            results = []
            for it in paged_items:
                if not isinstance(it, dict):
                    continue
                cite_key = it.get("cite_key")
                title = " ".join(str(it.get(k)) for k in ("author", "title", "year") if it.get(k))
                results.append(
                    {
                        "id": cite_key
                        or f"{it.get('source_id', '')}:w{it.get('work_id', '')}:p{it.get('passage_id', '')}",
                        "title": title or "Library passage",
                        "snippet": _library_snippet(it.get("text")),
                        "ref": cite_key or it.get("ref"),
                        "citation": it.get("citation"),
                        "cite_key": cite_key,
                        "cite_uri": it.get("cite_uri"),
                        "file_sha256": it.get("file_sha256"),
                        "work_sha256": it.get("work_sha256"),
                        "node": it.get("node"),
                        "source_id": it.get("source_id"),
                        "work_id": it.get("work_id"),
                        "passage_id": it.get("passage_id"),
                        "page_start": it.get("page_start"),
                        "page_end": it.get("page_end"),
                    }
                )
            return {
                "status": "ok",
                "source": "library",
                "results": results,
                "has_more": len(items) > (offset + effective_k),
            }

        elif source == "codebase":
            if not query or not query.strip():
                raise FleetArgumentError("query is required for codebase search")
            params = {"q": query, "k": min(100, effective_k + offset)}
            identity = run_id if run_id is not None else os.environ.get("STAFF_RUN_ID")
            if identity is not None:
                _check(len(identity) <= 120, "run_id must be at most 120 characters")
                params["run_id"] = _match(PATTERNS.ident, identity, "run_id")
            data = self.request("GET", "/api/v1/staff/knowledge/cartographer/search", params=params)
            items = data.get("passages", []) if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = []
            page = items[offset : offset + effective_k]
            results = [
                {
                    "id": item["citation"],
                    "title": item.get("title") or item.get("source") or "Codebase passage",
                    "snippet": _library_snippet(item.get("text")),
                    "ref": item.get("citation"),
                    "citation": item.get("citation"),
                    "repo": item.get("repo"),
                    "source": item.get("source"),
                    "commit": item.get("commit"),
                    "content_hash": item.get("content_hash"),
                    **{key: item.get(key) for key in ("anchor", "status", "authority", "authority_rank", "score")},
                }
                for item in page
                if isinstance(item, dict) and item.get("citation")
            ]
            return {
                "status": "ok",
                "source": "codebase",
                "stale": bool(data.get("stale", True)) if isinstance(data, dict) else True,
                **({"run_id": data.get("run_id")} if isinstance(data, dict) and "run_id" in data else {}),
                "results": results,
                "has_more": len(items) > (offset + effective_k),
            }

        elif source == "knowledge":
            params = {"q": query or "", "limit": effective_k + offset}
            pack_id = repo or "fleet"
            try:
                data = self.request("GET", f"/api/v1/staff/knowledge/{pack_id}/search", params=params)
                items = data.get("results", data.get("items", [])) if isinstance(data, dict) else []
            except Exception:
                items = []
            paged_items = items[offset : offset + effective_k]
            results = []
            for it in paged_items:
                raw_text = it.get("snippet") or it.get("text") or it.get("body") or ""
                results.append(
                    {
                        "id": it.get("id", ""),
                        "title": it.get("title", f"Knowledge: {pack_id}"),
                        "snippet": str(raw_text)[:400],
                        "ref": it.get("ref") or f"/api/v1/staff/knowledge/{pack_id}",
                    }
                )
            return {
                "status": "ok",
                "source": "knowledge",
                "results": results,
                "has_more": len(items) > (offset + effective_k),
            }

        elif source == "memory":
            params = {"tag": repo, "limit": effective_k + offset}
            data = self.request("GET", "/api/v1/memory", params={k: v for k, v in params.items() if v})
            items = data.get("entries", data.get("items", [])) if isinstance(data, dict) else []
            paged_items = items[offset : offset + effective_k]
            results = []
            for it in paged_items:
                raw_text = it.get("body") if isinstance(it, dict) else ""
                snippet = json.dumps(raw_text) if isinstance(raw_text, dict) else str(raw_text)
                results.append(
                    {
                        "id": it.get("id", ""),
                        "title": it.get("entry_type", "Memory item"),
                        "snippet": snippet[:400],
                        "ref": f"/api/v1/memory/{it.get('id', '')}",
                    }
                )
            return {
                "status": "ok",
                "source": "memory",
                "results": results,
                "has_more": len(items) > (offset + effective_k),
            }

        return {"status": "error", "message": f"unhandled source {source}"}

    def query_context(
        self,
        repo: str | None = None,
        op: str = "search",
        query: str | None = None,
        k: int = 5,
        offset: int = 0,
        full: bool = False,
    ) -> dict[str, Any]:
        return self.fleet_query(source="context", op=op, query=query, repo=repo, k=k, offset=offset, full=full)

    def query_library(
        self,
        query: str | None = None,
        repo: str | None = None,
        topic: str | None = None,
        k: int = 5,
        offset: int = 0,
        full: bool = False,
    ) -> dict[str, Any]:
        return self.fleet_query(
            source="library", op="search", query=query, repo=repo, topic=topic, k=k, offset=offset, full=full
        )

    def query_codebase(
        self, query: str | None = None, k: int = 5, offset: int = 0, run_id: str | None = None
    ) -> dict[str, Any]:
        """Search indexed committed code, optionally recording evidence for an owned run."""
        return self.fleet_query(source="codebase", query=query, k=k, offset=offset, run_id=run_id)

    def query_knowledge(
        self, query: str | None = None, repo: str | None = None, k: int = 5, offset: int = 0, full: bool = False
    ) -> dict[str, Any]:
        return self.fleet_query(source="knowledge", op="search", query=query, repo=repo, k=k, offset=offset, full=full)

    def query_memory(
        self, query: str | None = None, repo: str | None = None, k: int = 5, offset: int = 0, full: bool = False
    ) -> dict[str, Any]:
        return self.fleet_query(source="memory", op="search", query=query, repo=repo, k=k, offset=offset, full=full)

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

    # ------------------------------------------------------------------ proposals

    def submit_proposal(
        self,
        title: str,
        target_repos: list[str] | str,
        problem: str,
        evidence: str,
        options_considered: str | list[str],
        lean: str,
        estimated_cost: str,
        urgency: str,
        source: str | None = None,
        code_request_url: str | None = None,
        confirm_not_duplicate: bool = False,
        pull_request: str | None = None,
    ) -> Any:
        """Submit a proposal to the Board (requires ``proposals.write`` scope).

        ``pull_request`` (``owner/repo#N``) names a draft PR or review for the Board to read.
        """
        title_text = _text(title, "title", LIMITS.max_proposal_title)
        if isinstance(target_repos, str):
            repos = [r.strip() for r in target_repos.split(",") if r.strip()]
        else:
            _check(
                isinstance(target_repos, list) and bool(target_repos),
                "target_repos must be a non-empty list or string",
            )
            repos = [str(r).strip() for r in target_repos if str(r).strip()]
        _check(bool(repos), "target_repos cannot be empty")

        if isinstance(options_considered, list):
            options_text = "\n".join(f"- {o}" for o in options_considered)
        else:
            options_text = str(options_considered)

        estimated_cost_text = _text(estimated_cost, "estimated_cost", LIMITS.max_proposal_source, form="line")
        _check(
            estimated_cost_text in PROPOSAL_ESTIMATED_EFFORTS,
            f"estimated_cost must be one of {PROPOSAL_ESTIMATED_EFFORTS}",
        )
        urgency_text = _text(urgency, "urgency", LIMITS.max_proposal_source, form="line")
        _check(urgency_text in PROPOSAL_URGENCIES, f"urgency must be one of {PROPOSAL_URGENCIES}")

        body = {
            "title": title_text,
            "target_repos": repos,
            "problem": _text(problem, "problem", LIMITS.max_proposal_text),
            "evidence": _text(evidence, "evidence", LIMITS.max_proposal_text),
            "options_considered": _text(options_text, "options_considered", LIMITS.max_proposal_text),
            "lean": _text(lean, "lean", LIMITS.max_proposal_text),
            "estimated_cost": estimated_cost_text,
            "urgency": urgency_text,
            "source": _opt_text(source, "source", LIMITS.max_proposal_source) or self.agent or self.session or "agent",
            "code_request_url": _opt_text(code_request_url, "code_request_url", LIMITS.max_proposal_url),
            "pull_request": _opt_text(pull_request, "pull_request", LIMITS.max_proposal_url),
            "confirm_not_duplicate": bool(confirm_not_duplicate),
        }
        return self.request("POST", "/api/proposals", body=body)

    def list_proposals(self, state: str | None = None, repo: str | None = None) -> Any:
        """List Board proposals with optional state and repo filtering."""
        params: dict[str, Any] = {}
        if state is not None:
            params["state"] = state
        if repo is not None:
            params["repo"] = repo
        return self.request("GET", "/api/proposals", params=params)

    def get_proposal(self, number: int) -> Any:
        """Get detail and secretary comments for proposal ``number``."""
        return self.request("GET", f"/api/proposals/{_positive_int(number, 'number')}")


# ---------------------------------------------------------------------- doctor (#1802)
# Read-only connection diagnostics. Each check is a pure function of its inputs (injectable
# resolver / TLS probe / clock) returning a CheckResult; only ``run_doctor`` talks to the
# server, and it issues GET requests only unless ``with_roundtrip`` is set.

CLIENT_ENVELOPE_VERSION = 1
REQUIRED_SCOPES = ("staff.read", "staff.chat", "coordination.write")
BRIEFING_MAX_AGE_SECONDS = 300.0
ROUNDTRIP_ROLE = "barb"


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one doctor check. ``remedy`` is empty when ``ok``."""

    name: str
    ok: bool
    detail: str
    remedy: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pass(name: str, detail: str) -> CheckResult:
    return CheckResult(name, True, detail)


def _fail(name: str, detail: str, remedy: str) -> CheckResult:
    return CheckResult(name, False, detail, remedy)


def _tls_handshake(host: str, port: int) -> None:
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=5) as sock:
        with context.wrap_socket(sock, server_hostname=host):
            return


def check_dns_tls(
    base_url: str,
    *,
    resolver: Callable[[str, int], Any] = socket.getaddrinfo,
    tls_probe: Callable[[str, int], None] = _tls_handshake,
) -> CheckResult:
    """Resolve the host and, for https, complete a verified TLS handshake."""
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname or ""
    secure = parsed.scheme == "https"
    port = parsed.port or (443 if secure else 80)
    try:
        resolver(host, port)
    except OSError as exc:
        return _fail(
            "dns-tls",
            f"DNS lookup failed for {host}: {exc}",
            "Check FLEET_API_URL for typos; verify Tailscale / private DNS is connected.",
        )
    if not secure:
        return _pass("dns-tls", f"{host} resolves (plain http, no TLS)")
    try:
        tls_probe(host, port)
    except OSError as exc:
        return _fail(
            "dns-tls",
            f"TLS handshake with {host}:{port} failed: {exc}",
            "Check the server certificate (expiry, hostname, trust chain); do not disable verification.",
        )
    return _pass("dns-tls", f"{host} resolves and TLS handshake verified")


def _api_failure(name: str, exc: FleetAPIError) -> CheckResult:
    if exc.status == 0:
        return _fail(name, f"server unreachable: {exc.body}", "Check connectivity and that the dashboard is running.")
    if exc.status == 404:
        return _fail(name, "endpoint not found (HTTP 404)", "Server is older than this client; upgrade it.")
    return _fail(name, f"HTTP {exc.status}: {exc.to_envelope()['message']}", "Inspect the dashboard logs.")


def check_token(client: FleetClient) -> tuple[CheckResult, dict[str, Any] | None]:
    """Validate the bearer token against ``/api/auth/me``; return the principal on success."""
    if not client.token:
        return (
            _fail("token", "FLEET_API_TOKEN is not set", "Export FLEET_API_TOKEN=svc_... (docs/agents/connect.md)."),
            None,
        )
    try:
        principal = client.request("GET", "/api/auth/me")
    except FleetAPIError as exc:
        if exc.status == 401:
            return (
                _fail(
                    "token",
                    "token rejected (HTTP 401): it is revoked, expired or invalid",
                    "Mint a new token (docs/agents/connect.md) and re-export FLEET_API_TOKEN.",
                ),
                None,
            )
        if exc.status == 403:
            return _fail("token", "token forbidden (HTTP 403)", "Grant the principal a role in principals.yml."), None
        return _api_failure("token", exc), None
    if not isinstance(principal, dict):
        return _fail("token", "unexpected /api/auth/me response", "Check FLEET_API_URL points at the dashboard."), None
    return _pass("token", f"authenticated as {principal.get('id', 'unknown')}"), principal


def check_scopes(principal: dict[str, Any] | None, required: tuple[str, ...] = REQUIRED_SCOPES) -> CheckResult:
    """Fail naming every required scope the principal lacks."""
    if principal is None:
        return _fail("scopes", "no authenticated principal to inspect", "Fix the token check first.")
    granted = set(principal.get("scopes") or [])
    missing = [scope for scope in required if scope not in granted]
    if missing:
        return _fail(
            "scopes",
            f"missing scopes: {', '.join(missing)}",
            "Add the scopes to the principal in principals.yml and restart the dashboard.",
        )
    return _pass("scopes", f"all required scopes granted ({', '.join(required)})")


def check_version(payload: Any) -> CheckResult:
    """Check the server's dispatch-envelope range includes this client's version."""
    envelope = payload.get("envelope") if isinstance(payload, dict) else None
    if not isinstance(envelope, dict) or "min" not in envelope or "max" not in envelope:
        return _fail("version", "/api/version response has no envelope range", "Upgrade the dashboard server.")
    low, high = envelope["min"], envelope["max"]
    if not (low <= CLIENT_ENVELOPE_VERSION <= high):
        return _fail(
            "version",
            f"incompatible schema: server supports envelope {low}-{high}, client speaks {CLIENT_ENVELOPE_VERSION}",
            "Upgrade the fleet client (git pull clients/fleet) or the dashboard so the ranges overlap.",
        )
    return _pass("version", f"envelope {CLIENT_ENVELOPE_VERSION} within server range {low}-{high}")


def check_briefing_freshness(
    briefing: Any, *, now: datetime | None = None, max_age_seconds: float = BRIEFING_MAX_AGE_SECONDS
) -> CheckResult:
    """Fail when the briefing has no parsable ``generated_at`` or is older than the limit."""
    stamp = briefing.get("generated_at") if isinstance(briefing, dict) else None
    try:
        generated = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return _fail("briefing", f"briefing generated_at unparsable: {stamp!r}", "Upgrade the dashboard server.")
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)  # noqa: UP017 - py3.10 stdlib clients
    age = ((now or datetime.now(timezone.utc)) - generated).total_seconds()  # noqa: UP017
    if age > max_age_seconds:
        return _fail(
            "briefing",
            f"briefing is stale: generated {age:.0f}s ago (limit {max_age_seconds:.0f}s)",
            "Check the dashboard clock and briefing cache; restart the dashboard if it persists.",
        )
    return _pass("briefing", f"briefing generated {max(age, 0):.0f}s ago")


def check_roundtrip(client: FleetClient) -> CheckResult:
    """Opt-in only: opens a thread with Barb (a write). Never run by default."""
    try:
        thread = client.staff_thread_open(
            role=ROUNDTRIP_ROLE, title="fleetctl doctor round-trip", initial_message="fleetctl doctor ping"
        )
    except FleetAPIError as exc:
        return _api_failure("roundtrip", exc)
    if not (isinstance(thread, dict) and thread.get("id")):
        return _fail("roundtrip", "Barb thread opened without an id", "Check the staff chat service.")
    return _pass("roundtrip", f"opened Barb thread {thread['id']}")


def _fetch(name: str, client: FleetClient, path: str) -> tuple[CheckResult | None, Any]:
    try:
        return None, client.request("GET", path)
    except FleetAPIError as exc:
        return _api_failure(name, exc), None


def run_doctor(client: FleetClient, *, now: datetime | None = None, with_roundtrip: bool = False) -> list[CheckResult]:
    """Run every check; GET-only unless ``with_roundtrip``. Stops early if the server is unreachable."""
    dns_tls = check_dns_tls(client.base_url)
    if not dns_tls.ok:
        return [dns_tls]
    failure, _ = _fetch("reachability", client, "/api/health")
    if failure is not None:
        return [dns_tls, failure]
    results = [dns_tls, _pass("reachability", f"{client.base_url} answered /api/health")]
    token_result, principal = check_token(client)
    results += [token_result, check_scopes(principal)]
    version_failure, version = _fetch("version", client, "/api/version")
    results.append(version_failure or check_version(version))
    brief_failure, briefing = _fetch("briefing", client, "/api/coordination/briefing")
    results.append(brief_failure or check_briefing_freshness(briefing, now=now))
    if with_roundtrip:
        results.append(check_roundtrip(client))
    return results
