"""Pydantic models for the assistant memory bank."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ENTRY_TYPES = ("session_digest", "decision", "task", "transcript")
EntryType = Literal["session_digest", "decision", "task", "transcript"]

MAX_TAGS = 32
MAX_TAG_LEN = 64
DigestProduct = Literal["codex", "dashboard"]
DigestID = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]*$"),
]
DigestText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class SessionDigestAppendRequest(BaseModel):
    """Small, transcript-free payload for one conversation turn's shared digest."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_product: DigestProduct
    conversation_id: DigestID
    user_message_id: DigestID
    reported_origin_principal: str | None = Field(default=None, min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=1000)
    decisions: list[DigestText] = Field(default_factory=list, max_length=3)
    open_items: list[DigestText] = Field(default_factory=list, max_length=3)


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


class SessionDigestAppendResponse(BaseModel):
    entry: MemoryEntry
    replayed: bool


class MemoryListResponse(BaseModel):
    entries: list[MemoryEntry]


class MemoryAppendResponse(BaseModel):
    entry: MemoryEntry


class MemoryContextPack(BaseModel):
    session_digests: list[MemoryEntry]
    open_tasks: list[MemoryEntry]
    directives: list[dict[str, Any]]
    holds: list[dict[str, Any]]
