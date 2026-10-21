"""Design-by-contract bounds for assistant memory."""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import HTTPException

from memory.models import ENTRY_TYPES, EntryType

MAX_ENTRY_JSON_BYTES = int(os.environ.get("ASSISTANT_MEMORY_MAX_ENTRY_BYTES", "16384"))
MAX_TRANSCRIPT_JSON_BYTES = int(os.environ.get("ASSISTANT_MEMORY_MAX_TRANSCRIPT_BYTES", "8192"))
MAX_STORE_BYTES = int(os.environ.get("ASSISTANT_MEMORY_MAX_STORE_BYTES", str(8 * 1024 * 1024)))
CONTEXT_DIGEST_LIMIT = int(os.environ.get("ASSISTANT_MEMORY_CONTEXT_DIGESTS", "5"))
CONTEXT_TASK_LIMIT = int(os.environ.get("ASSISTANT_MEMORY_CONTEXT_TASKS", "20"))


def require_entry_type(entry_type: str) -> EntryType:
    if entry_type not in ENTRY_TYPES:
        raise HTTPException(status_code=422, detail=f"invalid entry_type: {entry_type}")
    return entry_type  # type: ignore[return-value]


def encoded_body_size(body: dict[str, Any], entry_type: EntryType) -> int:
    return len(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def enforce_entry_size(body: dict[str, Any], entry_type: EntryType) -> None:
    limit = MAX_TRANSCRIPT_JSON_BYTES if entry_type == "transcript" else MAX_ENTRY_JSON_BYTES
    size = encoded_body_size(body, entry_type)
    if size > limit:
        raise HTTPException(
            status_code=422,
            detail={"code": "entry_too_large", "max_bytes": limit, "size_bytes": size},
        )
