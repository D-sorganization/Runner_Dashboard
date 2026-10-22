"""Request contracts for the fleet agent inbox bus (epic #2155, slice 1)."""

from __future__ import annotations

from typing import Literal

from coordination.models import (
    AGENT_PATTERN,
    REPO_PATTERN,
    SESSION_PATTERN,
    bare_repo,
    message_text,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_BODY = 4000
MIN_LEASE = 30
MAX_LEASE = 8 * 3600
DEFAULT_LEASE = 300
MSG_TYPES = ("request", "status", "handoff", "blocked")
WORK_KINDS = ("issue", "pr")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegisterBody(_Body):
    session: str = Field(pattern=SESSION_PATTERN)
    role: str = Field(pattern=AGENT_PATTERN)
    provider: str = Field(min_length=1, max_length=64)
    node: str = Field(min_length=1, max_length=128)
    lease_seconds: int = Field(default=DEFAULT_LEASE, ge=MIN_LEASE, le=MAX_LEASE)

    @field_validator("provider", "node")
    @classmethod
    def _printable(cls, value: str) -> str:
        if not value.isprintable():
            raise ValueError("must be printable text")
        return value


class HeartbeatBody(_Body):
    session: str = Field(pattern=SESSION_PATTERN)
    lease_seconds: int = Field(default=DEFAULT_LEASE, ge=MIN_LEASE, le=MAX_LEASE)


class SendBody(_Body):
    session: str = Field(pattern=SESSION_PATTERN)
    to: str = Field(min_length=1, max_length=128)
    type: Literal["request", "status", "handoff", "blocked"]
    repo: str = Field(pattern=REPO_PATTERN)
    work_kind: Literal["issue", "pr"] | None = None
    work_number: int | None = Field(default=None, gt=0)
    body: str = Field(min_length=1, max_length=MAX_BODY)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=SESSION_PATTERN)

    @field_validator("repo")
    @classmethod
    def _repo(cls, value: str) -> str:
        return bare_repo(value)

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        return message_text(value)

    @model_validator(mode="after")
    def _work_item_required(self) -> SendBody:
        if self.work_number is None or self.work_kind is None:
            raise ValueError("work_kind and work_number are required (repo plus issue or PR number)")
        return self


class AckBody(_Body):
    session: str = Field(pattern=SESSION_PATTERN)
    message_id: str = Field(min_length=8, max_length=128)


def normalize_to_address(raw: str) -> str:
    text = raw.strip()
    if "#" in text:
        repo, num = text.split("#", 1)
        if not repo or not num.isdigit():
            raise ValueError("work item address must look like Repo#123")
        return f"{bare_repo(repo)}#{int(num)}"
    if not text or len(text) > 128:
        raise ValueError("invalid recipient")
    return text
