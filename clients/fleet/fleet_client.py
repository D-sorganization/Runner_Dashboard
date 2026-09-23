"""Stdlib-only Python client for the Runner Dashboard Fleet API (#1228, epic #1192).

One method per endpoint of the Fleet Coordination API contract v1 (staff, coordination,
priorities). Agents of any vendor (Claude Code, Codex, Gemini CLI, Grok Bot) use it
directly, through ``fleetctl.py`` or through the ``fleet_mcp.py`` MCP server.

Design by contract: every method validates its arguments *before* any network I/O and
raises :class:`FleetArgumentError` (a ``ValueError``) on a precondition violation. Any
non-2xx response or transport failure raises :class:`FleetAPIError` carrying the HTTP
status (``0`` when the server was unreachable) and the decoded response body.

Environment:
    FLEET_API_URL      base URL (default ``http://127.0.0.1:8321``)
    FLEET_API_TOKEN    bearer token (per-agent bot token; optional on loopback)
    FLEET_API_TIMEOUT  request timeout in seconds (default 30)
    FLEET_AGENT        default ``agent`` for presence/claims (e.g. ``claude``)
    FLEET_SESSION      default ``session`` for presence/messages/claims
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

__all__ = ["DEFAULT_URL", "FleetAPIError", "FleetArgumentError", "FleetClient"]

DEFAULT_URL = "http://127.0.0.1:8321"
DEFAULT_TIMEOUT = 30.0
USER_AGENT = "fleet-client/1.0 (+Runner_Dashboard clients/fleet)"

# Contract patterns. Bare repo names for staff dispatch (server rejects owner/path);
# coordination accepts an optional ``owner/`` prefix because board sessions may carry one.
_BARE_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_REPO = re.compile(r"^(?:[A-Za-z0-9-]{1,39}/)?[A-Za-z0-9._-]{1,100}$")
_ROLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,119}$")  # agent / session / run id / message id
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$")

MAX_TEXT = 4000  # message / intent / reason text
MAX_PROMPT = 20000  # matches RunBody.prompt on the server
MAX_DIRECTIVE_TEXT = 500
MAX_DIRECTIVES = 50
MAX_PATHS = 50
USAGE_GROUPS = ("provider", "role", "day")  # backend/staff/store.py
RUN_STATUSES = ("queued", "preparing", "running", "succeeded", "failed", "cancelled", "blocked")


class FleetArgumentError(ValueError):
    """A client-side precondition failed; nothing was sent."""


class FleetAPIError(Exception):
    """Non-2xx response (``status`` = HTTP code) or unreachable server (``status`` = 0)."""

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Fleet API error {status}: {json.dumps(body, default=str)[:500]}")

    def to_dict(self) -> dict[str, Any]:
        return {"error": "fleet_api_error", "status": self.status, "body": self.body}


# --------------------------------------------------------------------------- validators


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise FleetArgumentError(message)


def _match(pattern: re.Pattern[str], value: Any, name: str) -> str:
    _check(isinstance(value, str) and bool(pattern.match(value)) and ".." not in value, f"invalid {name}: {value!r}")
    return str(value)


def _positive_int(value: Any, name: str) -> int:
    _check(isinstance(value, int) and not isinstance(value, bool) and value >= 1, f"{name} must be an integer >= 1")
    return int(value)


def _text(value: Any, name: str, limit: int = MAX_TEXT, *, required: bool = True) -> str:
    _check(isinstance(value, str), f"{name} must be a string")
    stripped = str(value).strip()
    _check(bool(stripped) or not required, f"{name} must not be empty")
    _check(len(stripped) <= limit, f"{name} exceeds {limit} characters")
    return stripped


def _opt(pattern: re.Pattern[str], value: Any, name: str) -> str | None:
    return None if value is None else _match(pattern, value, name)


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values so the server applies its own defaults."""
    return {k: v for k, v in data.items() if v is not None}


def _validate_directive(item: Any, index: int) -> dict[str, Any]:
    _check(isinstance(item, dict), f"directives[{index}] must be an object")
    allowed = {"id", "text", "repo", "priority", "expires", "set_by"}
    unknown = set(item) - allowed
    _check(not unknown, f"directives[{index}] has unknown fields: {sorted(unknown)}")
    out: dict[str, Any] = {"text": _text(item.get("text"), f"directives[{index}].text", MAX_DIRECTIVE_TEXT)}
    priority = item.get("priority", 3)
    _check(
        isinstance(priority, int) and not isinstance(priority, bool) and 1 <= priority <= 5,
        f"directives[{index}].priority must be an integer 1-5",
    )
    out["priority"] = priority
    repo = item.get("repo", "*")
    if repo != "*":
        _match(_REPO, repo, f"directives[{index}].repo")
    out["repo"] = repo
    if item.get("id") is not None:
        out["id"] = _match(_IDENT, item["id"], f"directives[{index}].id")
    if item.get("expires") is not None:
        out["expires"] = _match(_ISO, item["expires"], f"directives[{index}].expires")
    if item.get("set_by") is not None:
        out["set_by"] = _match(_IDENT, item["set_by"], f"directives[{index}].set_by")
    return out


# --------------------------------------------------------------------------- client


class FleetClient:
    """Thin, validated wrapper over the dashboard's ``/api/staff``, ``/api/coordination``
    and ``/api/priorities`` endpoints. Every method returns the decoded JSON response."""

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
        self.agent = _opt(_IDENT, agent if agent is not None else (os.environ.get("FLEET_AGENT") or None), "agent")
        self.session = _opt(
            _IDENT, session if session is not None else (os.environ.get("FLEET_SESSION") or None), "session"
        )

    # ------------------------------------------------------------------ transport

    def _headers(self, has_body: bool) -> dict[str, str]:
        headers = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "User-Agent": USER_AGENT}
        if has_body:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, body: Any = None) -> Any:
        """Send one request and return the decoded JSON (or ``{"text": ...}`` for non-JSON)."""
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
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers(data is not None))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - scheme validated in __init__
                return _decode(resp.read())
        except urllib.error.HTTPError as exc:
            raise FleetAPIError(exc.code, _decode(exc.read())) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise FleetAPIError(0, {"error": "unreachable", "url": self.base_url, "reason": str(reason)}) from None

    def _session(self, session: str | None) -> str:
        value = session if session is not None else self.session
        _check(value is not None, "session is required (argument or FLEET_SESSION)")
        return _match(_IDENT, value, "session")

    def _agent(self, agent: str | None) -> str | None:
        return _opt(_IDENT, agent if agent is not None else self.agent, "agent")

    # ------------------------------------------------------------------ staff

    def staff_summary(self) -> Any:
        return self.request("GET", "/api/staff/summary")

    def staff_roster(self) -> Any:
        return self.request("GET", "/api/staff/roster")

    def staff_board(self, local: bool = False) -> Any:
        return self.request("GET", "/api/staff/board", params={"local": True} if local else None)

    def staff_runs(
        self, limit: int = 50, role: str | None = None, status: str | None = None, since: str | None = None
    ) -> Any:
        _check(isinstance(limit, int) and 1 <= limit <= 500, "limit must be an integer 1-500")
        _check(status is None or status in RUN_STATUSES, f"status must be one of {RUN_STATUSES}")
        params = {
            "limit": limit,
            "role": _opt(_ROLE, role, "role"),
            "status": status,
            "since": _opt(_ISO, since, "since"),
        }
        return self.request("GET", "/api/staff/runs", params=params)

    def run(self, run_id: str, events: int = 200) -> Any:
        _check(isinstance(events, int) and 0 <= events <= 500, "events must be an integer 0-500")
        rid = _match(_IDENT, run_id, "run_id")
        return self.request("GET", f"/api/staff/runs/{urllib.parse.quote(rid, safe='')}", params={"events": events})

    def staff_schedule(self) -> Any:
        return self.request("GET", "/api/staff/schedule")

    def holds(self) -> Any:
        return self.request("GET", "/api/staff/holds")

    def usage(self, since: str | None = None, group: str = "provider") -> Any:
        _check(group in USAGE_GROUPS, f"group must be one of {USAGE_GROUPS}")
        return self.request("GET", "/api/staff/usage", params={"since": _opt(_ISO, since, "since"), "group": group})

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
        """Dispatch (or with ``dry_run`` preview) a staff role run. Pre: one of issue/pr/prompt."""
        role = _match(_ROLE, role, "role")
        _check(issue is not None or pr is not None or bool(prompt), "one of issue, pr or prompt is required")
        body = {
            "repo": _opt(_BARE_REPO, repo, "repo (bare name, no owner)"),
            "issue": None if issue is None else _positive_int(issue, "issue"),
            "pr": None if pr is None else _positive_int(pr, "pr"),
            "prompt": None if prompt is None else _text(prompt, "prompt", MAX_PROMPT, required=False),
            "provider": _opt(_ROLE, provider, "provider"),
            "model": _opt(_IDENT, model, "model"),
            "machine": _match(_IDENT, machine, "machine"),
            "dry_run": bool(dry_run),
        }
        return self.request("POST", f"/api/staff/{role}/run", body=_compact(body))

    def cancel(self, run_id: str) -> Any:
        rid = _match(_IDENT, run_id, "run_id")
        return self.request("POST", f"/api/staff/runs/{urllib.parse.quote(rid, safe='')}/cancel", body={})

    # ------------------------------------------------------------------ coordination

    def sessions(self, repo: str | None = None) -> Any:
        return self.request("GET", "/api/coordination/sessions", params={"repo": _opt(_REPO, repo, "repo")})

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
            _check(isinstance(paths, list) and len(paths) <= MAX_PATHS, f"paths must be a list of <= {MAX_PATHS}")
            paths = [_text(p, "paths[]", 300) for p in paths]
        _check(goals is None or isinstance(goals, dict), "goals must be an object")
        _check(
            ttl_hours is None or (isinstance(ttl_hours, (int, float)) and 0 < ttl_hours <= 8),
            "ttl_hours must be in (0, 8]",
        )
        body = {
            "agent": self._agent(agent),
            "session": self._session(session),
            "repo": _match(_REPO, repo, "repo"),
            "issue": _positive_int(issue, "issue"),  # RM presence requires issue + branch
            "branch": _match(_IDENT, branch, "branch"),
            "paths": paths,
            "goals": goals,
            "ttl_hours": ttl_hours,
        }
        return self.request("POST", "/api/coordination/presence", body=_compact(body))

    def release_presence(self, repo: str, session: str | None = None) -> Any:
        body = {"session": self._session(session), "repo": _match(_REPO, repo, "repo")}
        return self.request("POST", "/api/coordination/presence/release", body=body)

    def send_message(self, repo: str, to: str, text: str, session: str | None = None) -> Any:
        body = {
            "session": self._session(session),
            "repo": _match(_REPO, repo, "repo"),
            "to": _match(_IDENT, to, "to"),
            "text": _text(text, "text"),
        }
        return self.request("POST", "/api/coordination/messages", body=body)

    def ack(self, repo: str, message_id: str, session: str | None = None) -> Any:
        body = {
            "session": self._session(session),
            "repo": _match(_REPO, repo, "repo"),
            "message_id": _match(_IDENT, message_id, "message_id"),
        }
        return self.request("POST", "/api/coordination/messages/ack", body=body)

    def check_claim(self, repo: str, issue: int) -> Any:
        params = {"repo": _match(_REPO, repo, "repo"), "issue": _positive_int(issue, "issue")}
        return self.request("GET", "/api/coordination/claims", params=params)

    def claim(
        self, repo: str, issue: int, intent: str = "", agent: str | None = None, session: str | None = None
    ) -> Any:
        """Lease an issue. Raises FleetAPIError(409) when another agent holds it."""
        body = {
            "repo": _match(_REPO, repo, "repo"),
            "issue": _positive_int(issue, "issue"),
            "agent": self._agent(agent),
            "session": self._session(session),
            "intent": _text(intent, "intent", required=False),
        }
        return self.request("POST", "/api/coordination/claims", body=_compact(body))

    def release_claim(
        self, repo: str, issue: int, reason: str = "", agent: str | None = None, session: str | None = None
    ) -> Any:
        body = {
            "repo": _match(_REPO, repo, "repo"),
            "issue": _positive_int(issue, "issue"),
            "agent": self._agent(agent),
            "session": self._session(session),
            "reason": _text(reason, "reason", required=False),
        }
        return self.request("POST", "/api/coordination/claims/release", body=_compact(body))

    def briefing(self, repo: str | None = None, agent: str | None = None) -> Any:
        params = {"repo": _opt(_REPO, repo, "repo"), "agent": self._agent(agent)}
        return self.request("GET", "/api/coordination/briefing", params=params)

    # ------------------------------------------------------------------ priorities

    def priorities(self) -> Any:
        return self.request("GET", "/api/priorities")

    def meetings(self) -> Any:
        return self.request("GET", "/api/priorities/meetings")

    def meeting(self, date: str) -> Any:
        return self.request("GET", f"/api/priorities/meetings/{_match(_DATE, date, 'date (YYYY-MM-DD)')}")

    def directives(self) -> Any:
        return self.request("GET", "/api/priorities/directives")

    def set_directives(self, directives: list[dict[str, Any]]) -> Any:
        """Replace the operator directive list (write auth). Each item: text, priority 1-5, repo|"*"."""
        _check(isinstance(directives, list), "directives must be a list")
        _check(len(directives) <= MAX_DIRECTIVES, f"at most {MAX_DIRECTIVES} directives")
        items = [_validate_directive(item, i) for i, item in enumerate(directives)]
        return self.request("PUT", "/api/priorities/directives", body={"directives": items})


def _decode(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text[:2000]}
