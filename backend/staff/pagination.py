"""Cursor-based pagination helpers and response models for Staff API v1 (SC-F3, Issue #1312).

Specifications:
- Cursor pagination for runs, threads, messages, work items, and audit.
- Cursors are URL-safe base64 encoded strings of (timestamp, identifier).
- Invalid or corrupt cursors reject safely with 400 Bad Request and code "invalid_cursor".
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, Field

T = TypeVar("T")


class CursorPage(BaseModel, Generic[T]):
    """Standard pagination wrapper for v1 collection endpoints."""

    items: list[T] = Field(default_factory=list, description="List of items in the current page")
    next_cursor: str | None = Field(default=None, description="Cursor for the subsequent page, if any")
    prev_cursor: str | None = Field(default=None, description="Cursor for the preceding page, if any")
    has_more: bool = Field(default=False, description="True if more items follow the current page")


def encode_cursor(created_at: str, item_id: str) -> str:
    """Encode an item's timestamp and identifier into an opaque, URL-safe cursor string."""
    payload = json.dumps({"ts": created_at, "id": item_id})
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[str, str]:
    """Decode a URL-safe cursor string into (timestamp, identifier).

    Raises HTTPException(400) with code "invalid_cursor" if decoding or parsing fails.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict) or "ts" not in data or "id" not in data:
            raise ValueError("Missing required fields in cursor payload")
        return str(data["ts"]), str(data["id"])
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_cursor",
                "message": f"Malformed or invalid pagination cursor: {exc}",
                "retryable": False,
                "hint": "Pass the next_cursor token returned from a previous response without modification.",
            },
        ) from exc


def paginate_items(
    items: list[T],
    limit: int,
    cursor: str | None = None,
    key_fn: Callable[[T], tuple[str, str]] | None = None,
) -> CursorPage[T]:
    """Slice and compute cursor metadata for a pre-sorted list of items (newest to oldest)."""
    assert limit > 0, "limit must be positive"  # noqa: S101

    if key_fn is None:

        def default_key_fn(item: Any) -> tuple[str, str]:
            if isinstance(item, dict):
                ts = item.get("created_at") or item.get("ts") or ""
                item_id = item.get("id") or ""
            else:
                ts = getattr(item, "created_at", None) or getattr(item, "ts", "")
                item_id = getattr(item, "id", "")
            return str(ts), str(item_id)

        key_fn = default_key_fn

    start_idx = 0
    if cursor:
        cur_ts, cur_id = decode_cursor(cursor)
        for i, itm in enumerate(items):
            itm_ts, itm_id = key_fn(itm)
            # Find the item matching or immediately following the cursor
            if (itm_ts < cur_ts) or (itm_ts == cur_ts and itm_id < cur_id):
                start_idx = i
                break
        else:
            # If cursor is older than all items in the list
            start_idx = len(items)

    page_slice = items[start_idx : start_idx + limit]
    has_more = (start_idx + limit) < len(items)

    next_cursor = None
    if has_more and page_slice:
        last_item = page_slice[-1]
        last_ts, last_id = key_fn(last_item)
        next_cursor = encode_cursor(last_ts, last_id)

    prev_cursor = None
    if start_idx > 0 and page_slice:
        first_item = page_slice[0]
        first_ts, first_id = key_fn(first_item)
        prev_cursor = encode_cursor(first_ts, first_id)

    return CursorPage[T](
        items=page_slice,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        has_more=has_more,
    )
