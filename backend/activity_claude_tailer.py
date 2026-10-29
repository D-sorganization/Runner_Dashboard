"""Opt-in Claude Code local session JSONL tailer (#2871, epic #2856 slice T5a)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from activity import REDACTION_VERSION, ActivityEnvelope, ActivityStore, get_activity_store, redact_sensitive_content
from activity_transcript import (
    TranscriptChunkStore,
    emit_capture_gap,
    get_transcript_chunk_store,
    redact_terminal_stream,
)
from staff.cli_projects import projects_root

log = logging.getLogger("dashboard.activity_claude_tailer")

PARSER_VERSION = 1
CHECKPOINT_SCHEMA_VERSION = 1
SUPPORTED_FORMAT_VERSIONS: frozenset[int | str] = frozenset({1, "1", "v1", "1.0"})
KNOWN_RECORD_TYPES: frozenset[str] = frozenset(
    {"user", "assistant", "system", "tool_use", "tool_result", "summary", "message", "result"}
)
_TRUTHY_ENV: frozenset[str] = frozenset({"1", "true", "yes", "on"})
_SAFE_SESSION_RE = re.compile(r"[^A-Za-z0-9._:-]+")
_HEALTH_LOCK = threading.Lock()
_HEALTH_COUNTERS: dict[str, int] = {
    "warnings_total": 0,
    "unsupported_version_total": 0,
    "malformed_record_total": 0,
    "unrecognized_record_total": 0,
}


@dataclass(frozen=True)
class AdapterHealthWarning:
    session_id: str
    seq: int
    reason: str
    detail: str
    file_path: str


@dataclass(frozen=True)
class TailPassResult:
    enabled: bool
    files_scanned: int = 0
    chunks_written: int = 0
    gaps_emitted: int = 0
    warnings: tuple[AdapterHealthWarning, ...] = ()


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


def is_claude_tailer_enabled(env: Mapping[str, str] | None = None) -> bool:
    src = env if env is not None else os.environ
    return src.get("RUNNER_DASHBOARD_CLAUDE_TAILER_ENABLED", "").strip().lower() in _TRUTHY_ENV


def default_claude_session_roots(env: Mapping[str, str] | None = None) -> list[Path]:
    src = env if env is not None else os.environ
    explicit = src.get("RUNNER_DASHBOARD_CLAUDE_SESSION_DIRS", "").strip()
    if explicit:
        return [Path(p).expanduser() for p in explicit.split(os.pathsep) if p.strip()]
    home = Path(src.get("HOME") or Path.home()).expanduser()
    return [projects_root(src), home / ".config" / "Claude" / "claude-code-sessions"]


def _validate_identifier(value: str, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned or len(cleaned) > 128:
        raise ValueError(f"{label} must be 1..128 characters")
    if redact_sensitive_content(cleaned) != cleaned:
        raise ValueError(f"{label} must not contain sensitive content")
    return cleaned


def _derive_session_id(path: Path, record: dict[str, Any] | None = None) -> str:
    raw = ""
    if isinstance(record, dict):
        for key in ("session_id", "sessionId", "conversation_id"):
            val = record.get(key)
            if isinstance(val, str) and val.strip():
                raw = val.strip()
                break
    sanitized = _SAFE_SESSION_RE.sub("-", raw or path.stem).strip("-") or "unknown"
    prefixed = sanitized if sanitized.startswith("claude-") else f"claude-{sanitized}"
    return _validate_identifier(prefixed[:128], "session_id")


def _extract_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                btype = str(item.get("type") or "block")
                if btype == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"].strip())
                elif btype == "tool_use":
                    inp = json.dumps(item.get("input", {}), sort_keys=True, ensure_ascii=True)
                    parts.append(f"[tool_use:{item.get('name') or 'tool'}] {inp}")
                elif btype == "tool_result":
                    parts.append(f"[tool_result] {_extract_content_text(item.get('content'))}")
        return " | ".join(p for p in parts if p)
    if isinstance(content, dict):
        text = content.get("text")
        return text.strip() if isinstance(text, str) else _extract_content_text(content.get("content"))
    return ""


def parse_claude_jsonl_line(raw_line: str) -> tuple[dict[str, Any] | None, str | None, str | None, str | None]:
    """Parse one JSONL line under v1 rules; return ``(record, rendered, err_reason, err_detail)``."""
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
    if isinstance(parsed.get("message"), dict):
        msg = parsed["message"]
        role = str(msg.get("role") or role).strip()
        text = _extract_content_text(msg.get("content"))
    elif "content" in parsed:
        text = _extract_content_text(parsed.get("content"))
    elif isinstance(parsed.get("text"), str):
        text = parsed["text"].strip()
    elif isinstance(parsed.get("summary"), str):
        text = parsed["summary"].strip()

    if not text and (not isinstance(rec_type, str) or rec_type not in KNOWN_RECORD_TYPES):
        return parsed, None, "unrecognized_record_format", f"keys={sorted(parsed.keys())[:6]}"
    return parsed, (f"[{role}] {text}\n" if text else f"[{role}]\n"), None, None


class ClaudeSessionTailer:
    """Opt-in per-node Claude Code JSONL tailer with durable offset checkpointing."""

    def __init__(
        self,
        *,
        roots: Sequence[str | Path] | None = None,
        activity_store: ActivityStore | None = None,
        chunk_store: TranscriptChunkStore | None = None,
        checkpoint_path: str | Path | None = None,
        node: str,
        principal: str = "agent-claude",
        enabled: bool | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.node, self.principal = _validate_identifier(node, "node"), _validate_identifier(principal, "principal")
        self._env = env if env is not None else os.environ
        self._explicit_enabled = enabled
        self.roots = [Path(r).expanduser() for r in roots] if roots is not None else default_claude_session_roots(env)
        self.activity_store = activity_store or get_activity_store()
        self.chunk_store = chunk_store or get_transcript_chunk_store()
        default_cp = Path(self._env.get("RUNNER_DASHBOARD_CONFIG_DIR", "~/.config/runner-dashboard")).expanduser()
        self.checkpoint_path = (
            Path(checkpoint_path).expanduser() if checkpoint_path else default_cp / "claude_tailer_checkpoints.json"
        )

    def is_enabled(self) -> bool:
        return (
            bool(self._explicit_enabled) if self._explicit_enabled is not None else is_claude_tailer_enabled(self._env)
        )

    def _load_checkpoints(self) -> dict[str, dict[str, int]]:
        if not self.checkpoint_path.exists():
            return {}
        try:
            data = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"corrupt checkpoint file {self.checkpoint_path}: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(f"unsupported checkpoint schema_version in {self.checkpoint_path}")
        files = data.get("files")
        if not isinstance(files, dict):
            raise ValueError("malformed checkpoint files map")
        out: dict[str, dict[str, int]] = {}
        for key, entry in files.items():
            if isinstance(entry, dict):
                offset, next_seq = int(entry.get("offset", 0)), int(entry.get("next_seq", 1))
                if offset < 0 or next_seq < 1:
                    raise ValueError("invalid negative checkpoint offset or seq")
                out[str(key)] = {"offset": offset, "next_seq": next_seq}
        return out

    def _save_checkpoints(self, checkpoints: dict[str, dict[str, int]]) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.checkpoint_path.with_suffix(".tmp")
        doc = {"schema_version": CHECKPOINT_SCHEMA_VERSION, "files": checkpoints}
        tmp.write_text(json.dumps(doc, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.checkpoint_path)

    def _discover_jsonl_files(self) -> list[Path]:
        return [
            p
            for r in self.roots
            if r.is_dir() and not r.is_symlink()
            for p in sorted(r.rglob("*.jsonl"))
            if p.is_file() and not p.is_symlink()
        ]

    def _ingest_chunk_and_meta(self, session_id: str, seq: int, rendered: str) -> None:
        body = redact_terminal_stream(rendered).encode("utf-8")
        now_ts = time.time()
        self.chunk_store.put_chunk(
            session_id=session_id, seq=seq, node=self.node, principal=self.principal, body=body, stored_at=now_ts
        )
        raw_env = {
            "schema_version": 1,
            "event_id": f"desktop_adapter:{session_id}:{seq}",
            "source": "desktop_adapter",
            "principal": self.principal,
            "node": self.node,
            "session_id": session_id,
            "seq": seq,
            "ts": datetime.fromtimestamp(now_ts, tz=UTC),
            "kind": "output",
            "redaction_version": REDACTION_VERSION,
            "payload": {"bytes": len(body), "adapter": "claude_code", "parser_version": PARSER_VERSION},
        }
        self.activity_store.ingest([ActivityEnvelope.model_validate(raw_env)], principal=self.principal)

    def tail_once(self) -> TailPassResult:
        if not self.is_enabled():
            return TailPassResult(enabled=False)

        checkpoints = self._load_checkpoints()
        files = self._discover_jsonl_files()
        chunks_written = gaps_emitted = 0
        warnings: list[AdapterHealthWarning] = []

        for file_path in files:
            key = str(file_path.resolve())
            state = checkpoints.get(key, {"offset": 0, "next_seq": 1})
            offset, next_seq = state["offset"], state["next_seq"]
            try:
                raw_bytes = file_path.read_bytes()
            except OSError as exc:
                log.warning("claude session tailer could not read %s: %s", file_path.name, exc)
                continue
            cursor = 0 if len(raw_bytes) < offset else offset

            while cursor < len(raw_bytes):
                nl_index = raw_bytes.find(b"\n", cursor)
                if nl_index == -1:
                    break
                line_str = raw_bytes[cursor : nl_index + 1].decode("utf-8", errors="replace").strip()
                cursor = nl_index + 1
                if not line_str:
                    continue

                seq, next_seq = next_seq, next_seq + 1
                record, rendered, err_reason, err_detail = parse_claude_jsonl_line(line_str)
                session_id = _derive_session_id(file_path, record)
                if err_reason is not None or rendered is None:
                    detail = redact_sensitive_content(err_detail or err_reason or "parse_error")
                    reason = err_reason or "parse_error"
                    _record_warning(reason)
                    warnings.append(AdapterHealthWarning(session_id, seq, reason, detail, file_path.name))
                    log.warning(
                        "claude tailer warning: session=%s seq=%d reason=%s detail=%s", session_id, seq, reason, detail
                    )
                    gap_extra = {"adapter": "claude_code", "detail": detail, "parser_version": PARSER_VERSION}
                    emit_capture_gap(
                        self.activity_store, session_id, seq, self.principal, self.node, reason, extra=gap_extra
                    )
                    gaps_emitted += 1
                else:
                    self._ingest_chunk_and_meta(session_id, seq, rendered)
                    chunks_written += 1

            if cursor != offset or next_seq != state["next_seq"]:
                checkpoints[key] = {"offset": cursor, "next_seq": next_seq}
                self._save_checkpoints(checkpoints)

        return TailPassResult(True, len(files), chunks_written, gaps_emitted, tuple(warnings))
