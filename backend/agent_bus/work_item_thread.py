"""Work-item thread metadata for the agent bus (#2155).

Slice 1 creates one staff thread per ``repo`` + issue/PR. Slice 3b will store a
structured, versioned handoff document on that thread's ``meta`` JSON — no new
SQLite tables or thread columns required.
"""

from __future__ import annotations

from typing import Any, Literal

# Bump only when the handoff document envelope shape changes (slice 3b+).
HANDOFF_DOCUMENT_SCHEMA_VERSION = 1

WorkKind = Literal["issue", "pr"]


def new_work_item_thread_meta(repo: str, number: int, work_kind: WorkKind) -> dict[str, Any]:
    """Meta for a new agent-bus work-item thread.

    ``handoff_document`` is reserved for slice 3b. When set, it must be an object with:
    ``schema_version`` (int), ``revision`` (monotonic int), ``updated_at`` (ISO-8601 Z),
    and ``body`` (structured handoff fields). ``None`` means no document yet.
    """
    return {
        "agent_bus_work_item": True,
        "repo": repo,
        "number": number,
        "work_kind": work_kind,
        "handoff_document": None,
    }


def ensure_handoff_document_slot(meta: dict[str, Any]) -> dict[str, Any]:
    """Return ``meta`` with the slice 3b handoff slot present (additive, in-place safe)."""
    if not meta.get("agent_bus_work_item"):
        return meta
    out = dict(meta)
    if "handoff_document" not in out:
        out["handoff_document"] = None
    return out
