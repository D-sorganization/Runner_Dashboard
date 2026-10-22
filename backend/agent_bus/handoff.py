from __future__ import annotations

from typing import Any

from agent_bus.work_item_thread import HANDOFF_DOCUMENT_SCHEMA_VERSION

REQUIRED_BODY_KEYS = frozenset({"summary", "next_step"})


def validate_handoff_body(body: dict[str, Any]) -> dict[str, Any]:
    missing = REQUIRED_BODY_KEYS - body.keys()
    if missing:
        raise ValueError(f"handoff body missing keys: {sorted(missing)}")
    out: dict[str, Any] = {}
    for key in REQUIRED_BODY_KEYS:
        val = body[key]
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"handoff body field {key!r} must be non-empty text")
        out[key] = val.strip()
    return out


def next_handoff_document(
    existing: dict[str, Any] | None,
    body: dict[str, Any],
    *,
    updated_at: str,
) -> dict[str, Any]:
    revision = 1
    if existing and isinstance(existing.get("revision"), int):
        revision = int(existing["revision"]) + 1
    return {
        "schema_version": HANDOFF_DOCUMENT_SCHEMA_VERSION,
        "revision": revision,
        "updated_at": updated_at,
        "body": body,
    }
