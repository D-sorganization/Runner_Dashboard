"""Opt-in Codex CLI and desktop local session JSONL tailer (#2872, epic #2856 slice T5b)."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from activity_claude_tailer import (
    CHECKPOINT_SCHEMA_VERSION,
    PARSER_VERSION,
    SUPPORTED_FORMAT_VERSIONS,
    AdapterHealthWarning,
    ClaudeSessionTailer,
    TailPassResult,
    _extract_content_text,
)

__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "CODEX_KNOWN_RECORD_TYPES",
    "PARSER_VERSION",
    "SUPPORTED_FORMAT_VERSIONS",
    "AdapterHealthWarning",
    "CodexSessionTailer",
    "TailPassResult",
    "adapter_health_snapshot",
    "default_codex_session_roots",
    "is_codex_tailer_enabled",
    "parse_codex_jsonl_line",
    "reset_adapter_health",
]

log = logging.getLogger("dashboard.activity_codex_tailer")

CODEX_KNOWN_RECORD_TYPES: frozenset[str] = frozenset(
    {
        "user",
        "assistant",
        "system",
        "tool_use",
        "tool_result",
        "summary",
        "message",
        "result",
        "response_item",
        "event_msg",
        "function_call",
        "function_call_output",
        "session_meta",
        "turn_context",
    }
)
_HEALTH_LOCK = threading.Lock()
_HEALTH_COUNTERS: dict[str, int] = {
    "warnings_total": 0,
    "unsupported_version_total": 0,
    "malformed_record_total": 0,
    "unrecognized_record_total": 0,
}


def reset_adapter_health() -> None:
    with _HEALTH_LOCK:
        for key in _HEALTH_COUNTERS:
            _HEALTH_COUNTERS[key] = 0


def adapter_health_snapshot() -> dict[str, int]:
    with _HEALTH_LOCK:
        return dict(_HEALTH_COUNTERS)


def _record_warning(reason: str) -> None:
    with _HEALTH_LOCK:
        _HEALTH_COUNTERS["warnings_total"] += 1
        if reason == "unsupported_format_version":
            _HEALTH_COUNTERS["unsupported_version_total"] += 1
        elif reason == "malformed_record":
            _HEALTH_COUNTERS["malformed_record_total"] += 1
        elif reason == "unrecognized_record_format":
            _HEALTH_COUNTERS["unrecognized_record_total"] += 1


def is_codex_tailer_enabled(env: Mapping[str, str] | None = None) -> bool:
    src = env if env is not None else os.environ
    return src.get("RUNNER_DASHBOARD_CODEX_TAILER_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def default_codex_session_roots(env: Mapping[str, str] | None = None) -> list[Path]:
    src = env if env is not None else os.environ
    explicit = (
        src.get("RUNNER_DASHBOARD_CODEX_SESSION_DIRS", "").strip()
        or src.get("STAFF_CODEX_SESSION_DIRS", "").strip()
    )
    if explicit:
        return [Path(p).expanduser() for p in explicit.split(os.pathsep) if p.strip()]
    home = Path(src.get("HOME") or Path.home()).expanduser()
    codex_home = Path(src.get("CODEX_HOME") or (home / ".codex")).expanduser()
    return [codex_home / "sessions", home / ".config" / "Codex" / "sessions"]


def _extract_codex_payload(payload: Mapping[str, Any], default_role: str) -> tuple[str, str]:
    ptype = str(payload.get("type") or default_role).strip()
    role = str(payload.get("role") or ptype or default_role).strip()
    if ptype == "function_call":
        name = str(payload.get("name") or "tool")
        args = payload.get("arguments")
        arg_str = args if isinstance(args, str) else json.dumps(args or {}, sort_keys=True, ensure_ascii=True)
        return role, f"[function_call:{name}] {arg_str}".strip()
    if ptype == "function_call_output":
        out = _extract_content_text(payload.get("output"))
        return role, f"[function_call_output] {out}".strip()
    if "content" in payload:
        return role, _extract_content_text(payload.get("content"))
    for key in ("message", "text", "summary", "output"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return role, val.strip()
    return role, ""


def parse_codex_jsonl_line(raw_line: str) -> tuple[dict[str, Any] | None, str | None, str | None, str | None]:
    """Parse one Codex JSONL line under v1 rules; return ``(record, rendered, err_reason, err_detail)``."""
    try:
        parsed = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        return None, None, "malformed_record", f"invalid JSON at pos {exc.pos}"
    if not isinstance(parsed, dict):
        return None, None, "malformed_record", "JSONL record must be an object"

    for ver_key in ("format_version", "schema_version", "version"):
        if ver_key in parsed and parsed[ver_key] is not None:
            ver = parsed[ver_key]
            if isinstance(ver, bool) or ver not in SUPPORTED_FORMAT_VERSIONS:
                return parsed, None, "unsupported_format_version", f"{ver_key}={ver!r}"

    rec_type = parsed.get("type")
    role = str(rec_type or "event").strip()
    text = ""
    if isinstance(parsed.get("payload"), dict):
        role, text = _extract_codex_payload(parsed["payload"], role)
    elif isinstance(parsed.get("message"), dict):
        msg = parsed["message"]
        role = str(msg.get("role") or role).strip()
        text = _extract_content_text(msg.get("content"))
    elif "content" in parsed:
        text = _extract_content_text(parsed.get("content"))
    elif isinstance(parsed.get("text"), str):
        text = parsed["text"].strip()
    elif isinstance(parsed.get("summary"), str):
        text = parsed["summary"].strip()

    if not text and (not isinstance(rec_type, str) or rec_type not in CODEX_KNOWN_RECORD_TYPES):
        return parsed, None, "unrecognized_record_format", f"keys={sorted(parsed.keys())[:6]}"
    return parsed, (f"[{role}] {text}\n" if text else f"[{role}]\n"), None, None


class CodexSessionTailer(ClaudeSessionTailer):
    """Opt-in per-node Codex CLI/desktop JSONL tailer sharing T5a's tailer core."""

    ADAPTER_NAME: str = "codex"
    SESSION_PREFIX: str = "codex"
    DEFAULT_PRINCIPAL: str = "agent-codex"
    DEFAULT_CHECKPOINT_FILENAME: str = "codex_tailer_checkpoints.json"
    _logger: logging.Logger = log

    def _default_roots(self, env: Mapping[str, str] | None) -> list[Path]:
        return default_codex_session_roots(env)

    def _env_enabled(self, env: Mapping[str, str]) -> bool:
        return is_codex_tailer_enabled(env)

    def _parse_line(self, raw_line: str) -> tuple[dict[str, Any] | None, str | None, str | None, str | None]:
        return parse_codex_jsonl_line(raw_line)

    def _record_health_warning(self, reason: str) -> None:
        _record_warning(reason)
