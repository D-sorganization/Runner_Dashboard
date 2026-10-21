"""Pydantic models for the assistant memory bank."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ENTRY_TYPES = ("session_digest", "decision", "task", "transcript")
EntryType = Literal["session_digest", "decision", "task", "transcript"]

MAX_TAGS = 32
MAX_TAG_LEN = 64


class MemoryAppendRequest(BaseModel):
    entry_type: EntryType
    source_product: str = Field(min_length=1, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    body: dict[str, Any]
    expires_at: str | None = None
    supersedes_id: str | None = None


class MemoryEntry(BaseModel):
    id: str
    entry_type: EntryType
    author_principal: str
    source_product: str
    tags: list[str] = Field(default_factory=list)
    body: dict[str, Any]
    created_at: str
    expires_at: str | None = None
    supersedes_id: str | None = None
    superseded_by: str | None = None


class MemoryListResponse(BaseModel):
    entries: list[MemoryEntry]


class MemoryAppendResponse(BaseModel):
    entry: MemoryEntry


class MemoryContextPack(BaseModel):
    session_digests: list[MemoryEntry]
    open_tasks: list[MemoryEntry]
    directives: list[dict[str, Any]]
    holds: list[dict[str, Any]]
