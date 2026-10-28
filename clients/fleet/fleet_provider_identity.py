"""Infer fleet agent/session identity from provider CLI environments (#2982)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from fleet_validators import PATTERNS, _match, default_session, short_host

_CONVERSATION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def infer_fleet_agent() -> str | None:
    """Return a fleet agent name from explicit config or the active provider environment."""
    explicit = os.environ.get("FLEET_AGENT", "").strip()
    if explicit:
        return _match(PATTERNS.agent, explicit, "agent")
    if _truthy("CLAUDECODE") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        return "claude"
    if os.environ.get("CODEX_SANDBOX", "").strip() or os.environ.get("CODEX_HOME", "").strip():
        return "codex"
    if _truthy("CURSOR_AGENT") or os.environ.get("CURSOR_TRACE_ID", "").strip():
        return "cursor"
    if _truthy("GEMINI_CLI") or os.environ.get("GEMINI_CLI_HOME", "").strip():
        return "gemini"
    if _truthy("AGY_CLI") or os.environ.get("ANTIGRAVITY_HOME", "").strip():
        return "agy"
    return None


def _session_from_hook_payload(payload: dict[str, Any], agent: str) -> str | None:
    for key in ("session_id", "sessionId", "conversationId", "conversation_id"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            candidate = raw.strip()
            if _CONVERSATION_ID_RE.match(candidate):
                digest = hashlib.sha256(candidate.encode()).hexdigest()[:12]
                return f"{agent}-{short_host()}-conv-{digest}"
            if candidate.startswith(f"{agent}-") and PATTERNS.session.match(candidate):
                return candidate
    return None


def infer_fleet_session(*, agent: str, hook_stdin: str = "") -> str:
    """Derive a fleet session id from env or provider hook stdin JSON."""
    explicit = os.environ.get("FLEET_SESSION", "").strip()
    if explicit:
        return _match(PATTERNS.session, explicit, "session")
    if hook_stdin.strip():
        try:
            parsed = json.loads(hook_stdin)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            from_payload = _session_from_hook_payload(parsed, agent)
            if from_payload:
                return from_payload
    return default_session(agent)


def infer_provider_label() -> str:
    if os.environ.get("FLEET_PROVIDER", "").strip():
        return os.environ.get("FLEET_PROVIDER", "").strip()[:64]
    agent = infer_fleet_agent()
    return agent or "unknown"
