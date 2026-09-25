"""Server contract limits, regex patterns, and client-side validators (#1228, #1323)."""

from __future__ import annotations

import datetime as dt
import json
import re
import socket
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------- server contract


@dataclass(frozen=True)
class _Patterns:
    session: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    agent: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    repo: re.Pattern[str] = re.compile(r"^(?:[A-Za-z0-9-]{1,39}/)?[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    bare_repo: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
    branch: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,199}$")
    message_id: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    role: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    ident: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,119}$")
    date: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    iso: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$")


@dataclass(frozen=True)
class _Limits:
    max_message_text: int = 4000
    max_intent: int = 200
    max_reason: int = 300
    max_prompt: int = 20000
    max_directive_text: int = 500
    max_directives: int = 100
    max_version: int = 64
    max_paths: int = 50


PATTERNS = _Patterns()
LIMITS = _Limits()
BROADCAST = "*"
USAGE_GROUPS = ("provider", "role", "day")
RUN_STATUSES = ("queued", "preparing", "running", "succeeded", "failed", "cancelled", "blocked")
PROPOSAL_DECISIONS = ("approved", "denied")

_TEXT_CONTROLS = "\n\t"
_SESSION_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
_MAX_SESSION = 128


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

    def to_envelope(self) -> dict[str, Any]:
        """SC-F3 classified error envelope for tool calls."""
        code = "unreachable" if self.status == 0 else f"http_{self.status}"
        if isinstance(self.body, dict) and "detail" in self.body and isinstance(self.body["detail"], str):
            message = self.body["detail"]
        elif isinstance(self.body, dict) and "message" in self.body:
            message = str(self.body["message"])
        else:
            message = f"Fleet API error {self.status}: {json.dumps(self.body, default=str)[:200]}"
        return {
            "error": "fleet_api_error",
            "status": self.status,
            "body": self.body,
            "code": code,
            "message": message,
            "retryable": self.status in (429, 502, 503, 504, 0),
        }


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise FleetArgumentError(message)


def _match(pattern: re.Pattern[str], value: Any, name: str) -> str:
    _check(isinstance(value, str) and bool(pattern.match(value)) and ".." not in value, f"invalid {name}: {value!r}")
    return str(value)


def _positive_int(value: Any, name: str) -> int:
    _check(isinstance(value, int) and not isinstance(value, bool) and value >= 1, f"{name} must be an integer >= 1")
    return int(value)


def _non_negative_int(value: Any, name: str) -> int:
    _check(isinstance(value, int) and not isinstance(value, bool) and value >= 0, f"{name} must be an integer >= 0")
    return int(value)


def _text(
    value: Any, name: str, limit: int = LIMITS.max_message_text, *, required: bool = True, form: str = "free"
) -> str:
    _check(isinstance(value, str), f"{name} must be a string")
    stripped = str(value).strip()
    _check(bool(stripped) or not required, f"{name} must not be empty")
    _check(len(stripped) <= limit, f"{name} exceeds {limit} characters")
    _check(form != "line" or stripped.isprintable(), f"{name} must be one line of printable text")
    _check(
        form != "message" or not any(ord(ch) < 32 and ch not in _TEXT_CONTROLS for ch in stripped),
        f"{name} must not contain control characters other than newline and tab",
    )
    _check(form != "no_crlf" or not ("\r" in stripped or "\n" in stripped), f"{name} must be a single line")
    return stripped


def _opt_text(value: Any, name: str, limit: int) -> str | None:
    return None if value is None else _text(value, name, limit, form="line")


def _opt(pattern: re.Pattern[str], value: Any, name: str) -> str | None:
    return None if value is None else _match(pattern, value, name)


def default_session(agent: str, host: str | None = None, day: dt.date | None = None) -> str:
    short_host = (host if host is not None else socket.gethostname()).split(".", 1)[0] or "host"
    stamp = (day or dt.datetime.now(dt.timezone.utc).date()).strftime("%Y%m%d")  # noqa: UP017
    prefix = f"{agent}-"
    middle = _SESSION_UNSAFE.sub("-", short_host)[: max(1, _MAX_SESSION - len(prefix) - len(stamp) - 1)]
    return f"{prefix}{middle}-{stamp}"


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


def _validate_directive(item: Any, index: int) -> dict[str, Any]:
    _check(isinstance(item, dict), f"directives[{index}] must be an object")
    allowed = {"id", "text", "repo", "priority", "expires"}
    unknown = set(item) - allowed
    _check(not unknown, f"directives[{index}] has unknown fields: {sorted(unknown)}")
    text = _text(item.get("text"), f"directives[{index}].text", LIMITS.max_directive_text, form="no_crlf")
    out: dict[str, Any] = {"text": text}
    priority = item.get("priority", 3)
    _check(
        isinstance(priority, int) and not isinstance(priority, bool) and 1 <= priority <= 5,
        f"directives[{index}].priority must be an integer 1-5",
    )
    out["priority"] = priority
    repo = item.get("repo", "*")
    if repo != "*":
        _match(PATTERNS.bare_repo, repo, f"directives[{index}].repo (bare name or '*')")
    out["repo"] = repo
    if item.get("id") is not None:
        out["id"] = _match(PATTERNS.ident, item["id"], f"directives[{index}].id")
    if item.get("expires") is not None:
        out["expires"] = _match(PATTERNS.iso, item["expires"], f"directives[{index}].expires")
    return out


def _decode(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text[:2000]}


def _resolve_session(configured: str | None, session: str | None, agent: str | None) -> str:
    value = session if session is not None else configured
    if value is None and agent is not None:
        value = default_session(agent)
    _check(value is not None, "session is required (argument, FLEET_SESSION, or an agent to derive one from)")
    checked = _match(PATTERNS.session, value, "session")
    _check(
        agent is None or checked.startswith(f"{agent}-"),
        f"session {checked!r} must start with '{agent}-' "
        "(sessions are bound to their agent; see docs/agents/connect.md)",
    )
    return checked
