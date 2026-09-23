"""Request contracts for the coordination write endpoints (issue #1229).

DbC: every field an RM script receives is validated here first, so a request
that reaches a subprocess is already well-formed. Patterns mirror the RM
board's own identifiers (bare repo names, whitespace-free session ids).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

SESSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$"
AGENT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
# An optional ``owner/`` prefix is accepted (board sessions may carry one) and stripped before RM sees it.
REPO_PATTERN = r"^(?:[A-Za-z0-9-]{1,39}/)?[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
BRANCH_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,199}$"
MESSAGE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$"
RECIPIENT_PATTERN = r"^(\*|[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127})$"
INTENT_PATTERN = r"^[A-Za-z0-9][^\r\n]{0,199}$"
MAX_TEXT = 4000


def bare_repo(value: str) -> str:
    """``owner/name`` → ``name``; rejects ``..`` anywhere. Pre: ``value`` matched ``REPO_PATTERN``."""
    if ".." in value:
        raise ValueError("repo must be a repository name, not a path")
    return value.rsplit("/", 1)[-1]


SCOPE_FORBIDDEN_CHARS = "\\:*?[]"


def normalize_scope_path(raw: str) -> str:
    """Normalise a presence scope to RM's rule (relative, no globs, no ``.``/``..`` parts).

    ``./backend/coordination/`` becomes ``backend/coordination``; anything RM would reject raises
    ``ValueError`` so the caller gets a 422 here instead of a 502 from the board script.
    """
    path = raw.strip()
    while path.startswith("./"):
        path = path[2:]
    path = path.rstrip("/")
    if not path or len(path) > 300 or path.startswith(("-", "/")):
        raise ValueError("paths must be relative, non-empty, at most 300 characters and not start with '-'")
    if any(ch in path for ch in SCOPE_FORBIDDEN_CHARS) or any(p in {"", ".", ".."} for p in path.split("/")):
        raise ValueError(f"path {raw!r} must be a normalised relative path without globs")
    return path


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _SessionRepo(_Body):
    session: str = Field(pattern=SESSION_PATTERN)
    repo: str = Field(pattern=REPO_PATTERN)

    @field_validator("repo")
    @classmethod
    def _repo(cls, value: str) -> str:
        return bare_repo(value)


class PresenceBody(_SessionRepo):
    """``register``: advertise what this session is working on (advisory, not a lock)."""

    agent: str | None = Field(default=None, pattern=AGENT_PATTERN)
    issue: int = Field(gt=0)
    branch: str = Field(pattern=BRANCH_PATTERN)
    paths: list[str] = Field(default_factory=list, max_length=50)
    goals: dict[str, str] = Field(default_factory=dict, max_length=20)
    ttl_hours: float = Field(default=2.0, ge=0.1, le=8.0)

    @field_validator("paths")
    @classmethod
    def _paths(cls, value: list[str]) -> list[str]:
        return [normalize_scope_path(p) for p in value]

    @field_validator("goals")
    @classmethod
    def _goals(cls, value: dict[str, str]) -> dict[str, str]:
        for key, outcome in value.items():
            if not key.strip() or "=" in key or key.startswith("-") or len(key) > 80 or len(outcome) > 500:
                raise ValueError("goal keys must be 1-80 characters without '=' or a leading '-'; outcomes <= 500")
        return value


class ReleaseBody(_SessionRepo):
    """``release``: drop this session's presence."""


class MessageBody(_SessionRepo):
    """``send``: message one session, or ``*`` for everyone working in ``repo``."""

    to: str = Field(pattern=RECIPIENT_PATTERN)
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class AckBody(_SessionRepo):
    """``ack``: confirm receipt (not agreement) of a message."""

    message_id: str = Field(pattern=MESSAGE_ID_PATTERN)


class ClaimBody(_SessionRepo):
    """Lease ``repo#issue`` for ``agent``; 409 when another agent holds it."""

    issue: int = Field(gt=0)
    agent: str | None = Field(default=None, pattern=AGENT_PATTERN)
    intent: str = Field(default="implement", pattern=INTENT_PATTERN)


class ClaimReleaseBody(_SessionRepo):
    """Release this agent's lease on ``repo#issue``."""

    issue: int = Field(gt=0)
    agent: str | None = Field(default=None, pattern=AGENT_PATTERN)
    reason: str = Field(default="work completed", min_length=1, max_length=300)
