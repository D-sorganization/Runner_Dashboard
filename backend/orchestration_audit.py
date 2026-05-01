"""Orchestration audit log — append-only NDJSON.

Extracted from server.py (issue #359).
The audit lock lives here so routers/orchestration.py and any future
module share the same lock instance (resolves cross-module lock
duplication noted in issue #344).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger("dashboard.orchestration_audit")

ORCHESTRATION_AUDIT_PATH = Path.home() / "actions-runners" / "dashboard" / "orchestration_audit.json"

# Single shared lock — only this module owns it.
_orchestration_audit_lock: asyncio.Lock = asyncio.Lock()

# Corruption counter: incremented on each unreadable line or OS error.
# Exposed via get_audit_log_corrupt_total() for metrics/health endpoints.
_audit_log_corrupt_total: int = 0


def _migrate_audit_to_ndjson_if_needed() -> None:
    """Migrate legacy single-JSON-array file to NDJSON in-place. Idempotent."""
    if not ORCHESTRATION_AUDIT_PATH.exists():
        return
    try:
        with ORCHESTRATION_AUDIT_PATH.open("r", encoding="utf-8") as fh:
            head = fh.read(1)
            if head != "[":
                return
            fh.seek(0)
            raw = fh.read().strip()
        if not raw:
            return
        entries = json.loads(raw)
        if not isinstance(entries, list):
            return
        tmp_path = ORCHESTRATION_AUDIT_PATH.with_suffix(".ndjson.tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
        tmp_path.replace(ORCHESTRATION_AUDIT_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("orchestration audit migration not applied: %s", exc)


def load_orchestration_audit(limit: int = 50, principal: str | None = None) -> list[dict]:
    """Tail the last `limit` orchestration audit entries from disk.

    Reads the NDJSON-formatted audit log line by line, keeping only the
    trailing `limit` entries via a bounded deque. Legacy single-JSON-array
    files are migrated lazily on first read.
    """
    global _audit_log_corrupt_total  # noqa: PLW0603
    if not ORCHESTRATION_AUDIT_PATH.exists():
        return []

    _migrate_audit_to_ndjson_if_needed()

    from collections import deque  # noqa: PLC0415

    tail: deque[dict] = deque(maxlen=limit if not principal else None)
    try:
        with ORCHESTRATION_AUDIT_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    _audit_log_corrupt_total += 1
                    continue
                if not isinstance(entry, dict):
                    _audit_log_corrupt_total += 1
                    continue
                if principal and entry.get("principal") != principal:
                    continue
                tail.append(entry)
    except OSError as exc:
        _audit_log_corrupt_total += 1
        log.warning("orchestration audit read failed: %s", exc)
        return []

    result = list(tail)
    return result[-limit:] if principal else result


async def append_orchestration_audit(entry: dict) -> None:
    """Append a single audit entry to the orchestration audit log.

    Atomic single-line write via O_APPEND — POSIX guarantees writes
    <= PIPE_BUF (4096 bytes) appear atomically when O_APPEND is used,
    so concurrent appends interleave by line rather than corrupting bytes.
    """
    async with _orchestration_audit_lock:
        try:
            _migrate_audit_to_ndjson_if_needed()
            ORCHESTRATION_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, separators=(",", ":")) + "\n"
            with ORCHESTRATION_AUDIT_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            log.warning("orchestration audit write failed: %s", exc)


def get_audit_log_corrupt_total() -> int:
    """Return the count of corrupt audit-log read events since process start."""
    return _audit_log_corrupt_total
