"""Router for staff conversation thread export and retention policy (Issue #1490).

Mounted under /api/v1/staff and /api/staff.

Endpoints:
- GET /threads/{thread_id}/export: Export thread as Markdown or JSON.
- GET /threads/{thread_id}/export.md: Convenience Markdown export.
- GET /threads/{thread_id}/export.json: Convenience JSON export.
- POST /retention/sweep: Trigger a manual retention archival sweep.
- GET /retention/status: Return retention policy and archive storage info.
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from identity import Principal, require_scope
from staff.conversations import (
    ConversationStore,
    ConversationsUnavailableError,
    get_conversation_store,
)
from staff.retention import (
    DEFAULT_RETENTION_DAYS,
    _default_archive_dir,
    export_thread_json,
    export_thread_markdown,
    run_retention_archival,
)

log = logging.getLogger("dashboard.staff.export")

router = APIRouter(tags=["staff-export"])


def _get_store_or_503() -> ConversationStore:
    store = get_conversation_store()
    if not store.status.available:
        raise HTTPException(
            status_code=503,
            detail=store.status.banner_message or "Conversation store degraded",
        )
    return store


def _render_export(
    store: ConversationStore,
    thread_id: str,
    fmt: str,
) -> Response:
    thread = store.get_thread(thread_id)
    if not thread:
        raise HTTPException(
            status_code=404,
            detail={"code": "thread_not_found", "message": f"Thread {thread_id} not found"},
        )
    messages = store.list_messages(thread_id, limit=5000)

    if fmt in ("json",):
        content = export_thread_json(thread, messages)
        media_type = "application/json; charset=utf-8"
        filename = f"thread-{thread_id}.json"
    else:
        content = export_thread_markdown(thread, messages)
        media_type = "text/markdown; charset=utf-8"
        filename = f"thread-{thread_id}.md"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-cache",
    }
    return Response(content=content, media_type=media_type, headers=headers)


@router.get(
    "/threads/{thread_id}/export",
)
async def export_thread(
    thread_id: str,
    format: str = Query(default="markdown", pattern="^(markdown|md|json)$"),  # noqa: A002
    caller: Principal = Depends(require_scope("staff.read")),
) -> Response:
    """Export a conversation thread in Markdown or JSON format."""
    store = _get_store_or_503()
    try:
        return _render_export(store, thread_id, format)
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/threads/{thread_id}/export.md",
)
async def export_thread_markdown_endpoint(
    thread_id: str,
    caller: Principal = Depends(require_scope("staff.read")),
) -> Response:
    """Convenience endpoint to download a thread as Markdown."""
    store = _get_store_or_503()
    try:
        return _render_export(store, thread_id, "markdown")
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/threads/{thread_id}/export.json",
)
async def export_thread_json_endpoint(
    thread_id: str,
    caller: Principal = Depends(require_scope("staff.read")),
) -> Response:
    """Convenience endpoint to download a thread as JSON."""
    store = _get_store_or_503()
    try:
        return _render_export(store, thread_id, "json")
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/retention/sweep",
    response_model_exclude_none=True,
)
async def trigger_retention_sweep(
    retention_days: int = Query(default=DEFAULT_RETENTION_DAYS, ge=1, le=3650),
    caller: Principal = Depends(require_scope("staff.admin")),
) -> dict[str, Any]:
    """Trigger a manual retention archival sweep across all staff entity stores."""
    try:
        summary = run_retention_archival(retention_days=retention_days)
        return {
            "ok": True,
            "summary": summary,
        }
    except Exception as exc:
        log.exception("Retention sweep failed")
        raise HTTPException(status_code=500, detail=f"Retention sweep failed: {exc}") from exc


@router.get(
    "/retention/status",
    response_model_exclude_none=True,
)
async def get_retention_status(
    caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Return data retention policy and archive directory status."""
    return {
        "retention_days": DEFAULT_RETENTION_DAYS,
        "archive_dir": str(_default_archive_dir()),
        "policy": "180-day retention with gzip JSONL archive before deletion",
    }
