"""Work-item staff threads for agent-bus messages (#2155)."""

from __future__ import annotations

from agent_bus.work_item_thread import ensure_handoff_document_slot, new_work_item_thread_meta
from staff.conversations import ConversationStore


def work_thread_id(repo: str, number: int) -> str:
    safe = repo.replace("/", "-")
    return f"wi-{safe}-{number}"


def ensure_work_thread(cstore: ConversationStore, repo: str, number: int, kind: str) -> str:
    tid = work_thread_id(repo, number)
    existing = cstore.get_thread(tid)
    if existing:
        patched = ensure_handoff_document_slot(existing.meta)
        if patched != existing.meta:
            cstore.update_thread(tid, meta=patched)
        return tid
    title = f"{repo}#{number} ({kind})"
    meta = new_work_item_thread_meta(repo, number, kind)  # type: ignore[arg-type]
    cstore.create_thread(
        title=title,
        kind="auto",
        participants=["barb"],
        created_by="agent-bus",
        thread_id=tid,
        meta=meta,
    )
    return tid
