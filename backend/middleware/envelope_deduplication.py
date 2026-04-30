"""Request envelope deduplication (replay detection) extracted from server.py (issue #299).

Provides:
- Replay detection using processed envelope tracking
- Thread-safe envelope processing history management
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

# Ensure backend module is in path for relative imports
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config_schema  # noqa: E402

log = logging.getLogger("dashboard")

_PROCESSED_ENVELOPES_PATH = Path.home() / "actions-runners" / "dashboard" / "processed_envelopes.json"
_processed_envelopes_lock: asyncio.Lock = asyncio.Lock()


def _load_processed_envelopes() -> dict[str, float]:
    """Load processed envelope IDs and expiration times from disk.

    Returns:
        Dict mapping envelope_id to expiration timestamp
    """
    if not _PROCESSED_ENVELOPES_PATH.exists():
        return {}
    try:
        data = json.loads(_PROCESSED_ENVELOPES_PATH.read_text())
        return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


async def is_envelope_replay(envelope_id: str) -> bool:
    """Check if envelope_id has already been processed (replay detection).

    Args:
        envelope_id: Unique envelope identifier

    Returns:
        True if this envelope was already processed and is still valid
    """
    async with _processed_envelopes_lock:
        processed = _load_processed_envelopes()
        now = datetime.now(UTC).timestamp()

        if envelope_id in processed:
            expires_at = processed[envelope_id]
            return expires_at > now

        return False


async def record_processed_envelope(envelope_id: str, ttl_seconds: int = 86400) -> None:
    """Record that envelope_id has been processed (for replay detection).

    Args:
        envelope_id: Unique envelope identifier
        ttl_seconds: Expiration time-to-live (default 86400 = 1 day)
    """
    async with _processed_envelopes_lock:
        processed = _load_processed_envelopes()
        now = datetime.now(UTC).timestamp()
        expires_at = now + ttl_seconds

        processed[envelope_id] = expires_at

        cleaned = {k: v for k, v in processed.items() if v > now}
        try:
            _PROCESSED_ENVELOPES_PATH.parent.mkdir(parents=True, exist_ok=True)
            config_schema.atomic_write_json(_PROCESSED_ENVELOPES_PATH, cleaned)
        except OSError as exc:
            log.warning("failed to record processed envelope: %s", exc)
