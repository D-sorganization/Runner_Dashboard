"""Pydantic shapes for the Board Proposals API (issue #1284, CR-7).

DbC: Every request and response passes through these models.
Fields mirror the real board-proposal issue form
(``Repository_Management/.github/ISSUE_TEMPLATE/board-proposal.yml``), not a
free-form guess: heading text, dropdown enums and the ``needs-decision``
label all trace back to that template so parsing a form-filed issue works.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# A line starting with 1-6 '#' (optionally indented up to 3 spaces, matching
# CommonMark's own heading rule) would be parsed by our own section splitter
# as a fake ``###`` section header. Reject it at the boundary instead of
# silently corrupting the rendered issue body (review #1444 defect 6).
_HEADING_INJECTION_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s")
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$")
_SOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,99}$")

MAX_TITLE_LENGTH = 200
MAX_TEXT_LENGTH = 10000
MAX_SHORT_TEXT_LENGTH = 500

# Mirrors the form's "Estimated Effort" and "Urgency" dropdowns exactly
# (issue #1284 review defect 2). Free text is not accepted for either.
EstimatedEffort = Literal["Low", "Medium", "High"]
Urgency = Literal["Routine", "Urgent", "Emergency"]


def sanitize_text(value: str, max_len: int = MAX_TEXT_LENGTH) -> str:
    """Sanitise text inputs by stripping dangerous control chars and whitespace."""
    if not isinstance(value, str):
        raise ValueError("Must be a string")
    cleaned = _CONTROL_CHARS.sub("", value).strip()
    if not cleaned:
        raise ValueError("Must not be empty")
    if len(cleaned) > max_len:
        raise ValueError(f"Exceeds maximum allowed length of {max_len} characters")
    if _HEADING_INJECTION_RE.search(cleaned):
        raise ValueError("Must not contain markdown heading lines (e.g. '### ...')")
    return cleaned


class CreateProposalRequest(BaseModel):
    """Payload for submitting a suggestion to the Board (POST /api/proposals)."""

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    target_repos: list[str] = Field(min_length=1, max_length=10)
    problem: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    evidence: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    options_considered: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    lean: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    estimated_cost: EstimatedEffort
    urgency: Urgency
    source: str | None = Field(default=None, max_length=100)
    code_request_url: str | None = Field(default=None, max_length=500)
    confirm_not_duplicate: bool = Field(default=False)

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, v: Any) -> str:
        return sanitize_text(str(v), MAX_TITLE_LENGTH)

    @field_validator("target_repos", mode="before")
    @classmethod
    def validate_target_repos(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            parts = [p.strip() for p in v.split(",") if p.strip()]
            if not parts:
                raise ValueError("target_repos cannot be empty")
            raw_items: list[str] = parts
        elif isinstance(v, list):
            raw_items = [str(item) for item in v if str(item).strip()]
            if not raw_items:
                raise ValueError("target_repos cannot be empty")
        else:
            raise ValueError("target_repos must be a list of repo strings or comma-separated string")

        cleaned: list[str] = []
        for item in raw_items:
            repo = sanitize_text(item, 100)
            if not _REPO_NAME_RE.match(repo):
                raise ValueError(f"target_repos entry {repo!r} is not a valid repo name (org/repo or repo)")
            cleaned.append(repo)
        return cleaned

    @field_validator("problem", "evidence", "lean", mode="before")
    @classmethod
    def validate_text_fields(cls, v: Any) -> str:
        return sanitize_text(str(v), MAX_TEXT_LENGTH)

    @field_validator("options_considered", mode="before")
    @classmethod
    def validate_options(cls, v: Any) -> str:
        if isinstance(v, list):
            joined = "\n".join(f"- {sanitize_text(str(item), 1000)}" for item in v if str(item).strip())
            return sanitize_text(joined, MAX_TEXT_LENGTH)
        return sanitize_text(str(v), MAX_TEXT_LENGTH)

    @field_validator("estimated_cost", "urgency", mode="before")
    @classmethod
    def validate_enum_fields(cls, v: Any) -> str:
        # Sanitised first (strips stray whitespace/control chars); Pydantic's
        # Literal type check below still enforces the exact form values.
        return sanitize_text(str(v), MAX_SHORT_TEXT_LENGTH)

    @field_validator("source", mode="before")
    @classmethod
    def validate_source(cls, v: Any) -> str | None:
        if not v:
            return None
        cleaned = sanitize_text(str(v), 100)
        if not _SOURCE_RE.match(cleaned):
            raise ValueError("source must be an agent id, staff role, or username (letters/digits/._@- only)")
        return cleaned

    @field_validator("code_request_url", mode="before")
    @classmethod
    def validate_code_request_url(cls, v: Any) -> str | None:
        if not v:
            return None
        cleaned = sanitize_text(str(v), 500)
        if not cleaned.startswith("https://"):
            raise ValueError("code_request_url must be an https URL")
        return cleaned


class DuplicateCandidate(BaseModel):
    """An open proposal that might match the new submission."""

    number: int
    title: str
    url: str
    target_repos: list[str] = Field(default_factory=list)


class ProposalComment(BaseModel):
    """A comment on a proposal issue, highlighting Board-Secretary notes."""

    id: int
    user: dict[str, Any]
    body: str
    created_at: str
    is_secretary: bool = False


class ProposalItem(BaseModel):
    """Summary of a proposal returned in listings."""

    number: int
    title: str
    target_repos: list[str] = Field(default_factory=list)
    problem: str = ""
    evidence: str = ""
    options_considered: str = ""
    lean: str = ""
    estimated_cost: str = ""
    urgency: str = ""
    source: str = "human"
    code_request_url: str | None = None
    state: str = "open"
    decision: str | None = None
    decision_labels: list[str] = Field(default_factory=list)
    meeting_date: str | None = None
    consensus_url: str | None = None
    html_url: str = ""
    created_at: str = ""
    updated_at: str = ""
    closed_at: str | None = None
    comments_count: int = 0


class ProposalDetail(ProposalItem):
    """Full detail of a single proposal, including discussion and Board-Secretary comments."""

    comments: list[ProposalComment] = Field(default_factory=list)


class ProposalsListResponse(BaseModel):
    """Response envelope for GET /api/proposals."""

    proposals: list[ProposalItem] = Field(default_factory=list)
    total: int = 0
