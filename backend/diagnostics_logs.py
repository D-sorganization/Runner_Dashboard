"""Diagnostics logging ring buffer and structured log queries (#2235).

Provides in-memory ring-buffer Handler, LOG_LEVEL validation, and query caps (<=500, <=256KB).
"""

from __future__ import annotations

import collections
import logging
import os
import re
import threading
from datetime import UTC, datetime
from typing import Any

from staff.redaction import redact_sensitive_content

_VALID_LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
_AUTH_HEADER_RE = re.compile(r"(?i)\bauthorization:\s*([^\r\n,;]+)")
MAX_RESPONSE_BYTES = 256 * 1024  # 256 KB response cap
MAX_LOG_MESSAGE_CHARS = 2000
MAX_QUERY_LIMIT = 500


def validate_log_level(raw: str) -> int:
    """DbC: validate and return standard logging level integer or raise ValueError."""
    clean = (raw or "").strip().upper()
    if clean not in _VALID_LEVELS:
        raise ValueError(f"Invalid LOG_LEVEL: {raw!r}. Expected one of {sorted(_VALID_LEVELS.keys())}")
    return _VALID_LEVELS[clean]


def _redact_log_text(text: str) -> str:
    """Redact sensitive tokens, keys, IPs, and authorization headers."""
    if not text:
        return text
    cleaned = _AUTH_HEADER_RE.sub("Authorization: [REDACTED]", text)
    return redact_sensitive_content(cleaned)[:MAX_LOG_MESSAGE_CHARS]


class LogRingBuffer:
    """Thread-safe bounded in-memory ring buffer for redacted log records."""

    def __init__(self, max_records: int = 5000, max_bytes: int = 5 * 1024 * 1024) -> None:
        self.max_records = max_records
        self.max_bytes = max_bytes
        self._records: collections.deque[dict[str, Any]] = collections.deque(maxlen=max_records)
        self._current_bytes = 0
        self._lock = threading.Lock()

    def add_record(
        self,
        timestamp: str,
        level: str,
        logger: str,
        message: str,
        run_id: str = "",
        request_id: str = "",
        principal_id: str = "",
    ) -> None:
        redacted_msg = _redact_log_text(message)
        entry_size = len(redacted_msg.encode("utf-8")) + len(logger.encode("utf-8")) + 120
        entry: dict[str, Any] = {
            "timestamp": timestamp,
            "level": level.upper(),
            "logger": logger,
            "message": redacted_msg,
            "run_id": run_id or "-",
            "request_id": request_id or "-",
            "principal_id": principal_id or "-",
            "_size": entry_size,
        }
        with self._lock:
            while self._records and (
                self._current_bytes + entry_size > self.max_bytes or len(self._records) >= self.max_records
            ):
                oldest = self._records.popleft()
                self._current_bytes = max(0, self._current_bytes - oldest.get("_size", 0))
            self._records.append(entry)
            self._current_bytes += entry_size

    def query(
        self,
        *,
        level: str | None = None,
        logger: str | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], bool]:
        if limit > MAX_QUERY_LIMIT:
            raise ValueError(f"limit must be <= {MAX_QUERY_LIMIT}")
        norm_level = level.strip().upper() if level else None
        target_int_level = _VALID_LEVELS.get(norm_level) if norm_level else None
        results: list[dict[str, Any]] = []
        truncated = False
        accumulated_bytes = 0

        with self._lock:
            for item in reversed(self._records):
                if norm_level:
                    lvl = item["level"]
                    if (target_int_level is not None and _VALID_LEVELS.get(lvl, 0) < target_int_level) or (
                        target_int_level is None and lvl != norm_level
                    ):
                        continue
                if (logger and not item["logger"].startswith(logger)) or (run_id and item["run_id"] != run_id):
                    continue
                if (request_id and item["request_id"] != request_id) or (since and item["timestamp"] < since):
                    continue
                item_bytes = item.get("_size", 100)
                if accumulated_bytes + item_bytes > MAX_RESPONSE_BYTES and results:
                    truncated = True
                    break
                results.append({k: v for k, v in item.items() if not k.startswith("_")})
                accumulated_bytes += item_bytes
                if len(results) >= limit:
                    break
        return results, truncated

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._current_bytes = 0


class LogRingBufferHandler(logging.Handler):
    """Logging handler that routes records into LogRingBuffer."""

    def __init__(self, buffer: LogRingBuffer) -> None:
        super().__init__()
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ts = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
            msg = self.format(record) if self.formatter else record.getMessage()
            r_id = getattr(record, "run_id", "") or ""
            req_id = getattr(record, "request_id", "") or ""
            p_id = getattr(record, "principal_id", "") or ""
            self.buffer.add_record(
                timestamp=ts,
                level=record.levelname,
                logger=record.name,
                message=msg,
                run_id="" if r_id == "-" else r_id,
                request_id="" if req_id == "-" else req_id,
                principal_id="" if p_id == "-" else p_id,
            )
        except Exception:  # noqa: BLE001
            self.handleError(record)


_GLOBAL_BUFFER: LogRingBuffer | None = None
_INIT_LOCK = threading.Lock()


def get_log_ring_buffer() -> LogRingBuffer:
    global _GLOBAL_BUFFER
    if _GLOBAL_BUFFER is None:
        with _INIT_LOCK:
            if _GLOBAL_BUFFER is None:
                max_rec = int(os.environ.get("LOG_RING_BUFFER_CAP", "5000"))
                _GLOBAL_BUFFER = LogRingBuffer(max_records=max_rec)
    return _GLOBAL_BUFFER


def attach_log_ring_buffer(root_logger: logging.Logger | None = None) -> LogRingBufferHandler:
    target = root_logger or logging.getLogger()
    buf = get_log_ring_buffer()
    for h in target.handlers:
        if isinstance(h, LogRingBufferHandler):
            return h
    handler = LogRingBufferHandler(buf)
    from request_context import RequestIdLogFilter

    handler.addFilter(RequestIdLogFilter())
    target.addHandler(handler)
    return handler
