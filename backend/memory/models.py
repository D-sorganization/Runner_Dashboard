"""Pydantic models for the assistant memory bank."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

ENTRY_TYPES = ("session_digest", "decision", "task", "transcript", "lesson")
EntryType = Literal["session_digest", "decision", "task", "transcript", "lesson"]

MAX_TAGS = 32
MAX_TAG_LEN = 64
DigestProduct = Literal["codex", "dashboard"]
DigestID = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]*$"),
]
DigestText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ReviewStatus = Literal["proposed", "accepted", "quarantined", "retired", "promoted"]
Visibility = Literal["fleet", "barb-only"]
RepositoryName = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]
SourceRunID = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")]
_REPOSITORY_NAME: TypeAdapter[str] = TypeAdapter(RepositoryName)


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
    model_config = ConfigDict(extra="allow")

    entry_type: EntryType
    source_product: str = Field(min_length=1, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    body: dict[str, Any]
    expires_at: str | None = None
    supersedes_id: str | None = None
    repo: RepositoryName | None = None
    source_run: SourceRunID | None = None
    review_status: ReviewStatus | None = None
    visibility: Visibility | None = None

    @model_validator(mode="after")
    def validate_lesson_repo(self) -> Self:
        """Keep the supported body.repo fallback subject to the same typed boundary."""
        if self.entry_type == "lesson" and self.repo is None and "repo" in self.body:
            self.repo = _REPOSITORY_NAME.validate_python(self.body["repo"])
        return self


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
    repo: str | None = None
    review_status: str | None = None
    source_run: str | None = None
    visibility: str | None = None
    reviewed_by: str | None = None
    accepted_at: str | None = None


class MemoryReviewRequest(BaseModel):
    review_status: ReviewStatus
    expires_at: str | None = None


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
