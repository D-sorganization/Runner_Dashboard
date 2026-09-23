"""Request contracts for the coordination write endpoints (issue #1229).

DbC: every field an RM script receives is validated here first, so a request
that reaches a subprocess is already well-formed and bad input is a 422, never
a 502 from RM. Patterns mirror RM ``shared_scripts/agent_messages.py``
(``_IDENTIFIER`` for sessions, recipients, message ids and goal keys; goal
outcomes <= 250; message text without control characters other than newline
and tab). Free text that RM writes into a lease comment (``intent``,
``reason``) or a ``--goal`` argument must be a single printable line: a line
break there could forge an ``<!-- agent-lease v1 -->`` block (#1244).
Agent roster membership is checked in ``service`` (it may need an RM read).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

IDENTIFIER = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"  # RM agent_messages._IDENTIFIER
SESSION_PATTERN = rf"^{IDENTIFIER}$"
AGENT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
# An optional ``owner/`` prefix is accepted (board sessions may carry one) and stripped before RM sees it.
REPO_PATTERN = r"^(?:[A-Za-z0-9-]{1,39}/)?[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
BRANCH_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,199}$"
MESSAGE_ID_PATTERN = rf"^{IDENTIFIER}$"
RECIPIENT_PATTERN = rf"^(\*|{IDENTIFIER})$"
GOAL_KEY_PATTERN = re.compile(IDENTIFIER)
MAX_TEXT = 4000
MAX_GOAL_OUTCOME = 250
MAX_GOALS = 20
TEXT_ALLOWED_CONTROLS = "\n\t"


def single_line(value: str, what: str) -> str:
    """Reject any non-printable character (CR, LF, NEL, U+2028, VT, ...). Pre: ``value`` already stripped."""
    if not value or not value.isprintable():
        raise ValueError(f"{what} must be one non-empty line of printable text")
    return value


def message_text(value: str) -> str:
    """RM ``_text``: non-blank, no control characters except newline and tab."""
    if not value.strip():
        raise ValueError("text must not be blank")
    if any(ord(ch) < 32 and ch not in TEXT_ALLOWED_CONTROLS for ch in value):
        raise ValueError("text must not contain control characters other than newline and tab")
    return value


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
    goals: dict[str, str] = Field(default_factory=dict, max_length=MAX_GOALS)
    ttl_hours: float = Field(default=2.0, ge=0.1, le=8.0)

    @field_validator("paths")
    @classmethod
    def _paths(cls, value: list[str]) -> list[str]:
        return [normalize_scope_path(p) for p in value]

    @field_validator("goals")
    @classmethod
    def _goals(cls, value: dict[str, str]) -> dict[str, str]:
        for key, outcome in value.items():
            if not GOAL_KEY_PATTERN.fullmatch(key):
                raise ValueError(f"goal key {key!r} must match {IDENTIFIER}")
            if len(single_line(outcome.strip(), "goal outcome")) > MAX_GOAL_OUTCOME:
                raise ValueError(f"goal outcomes are at most {MAX_GOAL_OUTCOME} characters")
        return {key: outcome.strip() for key, outcome in value.items()}


class ReleaseBody(_SessionRepo):
    """``release``: drop this session's presence."""


class MessageBody(_SessionRepo):
    """``send``: message one session, or ``*`` for everyone working in ``repo``."""

    to: str = Field(pattern=RECIPIENT_PATTERN)
    text: str = Field(min_length=1, max_length=MAX_TEXT)

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        return message_text(value)


class AckBody(_SessionRepo):
    """``ack``: confirm receipt (not agreement) of a message."""

    message_id: str = Field(pattern=MESSAGE_ID_PATTERN)


class ClaimBody(_SessionRepo):
    """Lease ``repo#issue`` for ``agent``; 409 when another agent holds it."""

    issue: int = Field(gt=0)
    agent: str | None = Field(default=None, pattern=AGENT_PATTERN)
    intent: str = Field(default="implement", min_length=1, max_length=200)

    @field_validator("intent")
    @classmethod
    def _intent(cls, value: str) -> str:
        if value.startswith("-"):
            raise ValueError("intent must not start with '-'")
        return single_line(value, "intent")


class ClaimReleaseBody(_SessionRepo):
    """Release this agent's lease on ``repo#issue``."""

    issue: int = Field(gt=0)
    agent: str | None = Field(default=None, pattern=AGENT_PATTERN)
    reason: str = Field(default="work completed", min_length=1, max_length=300)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return single_line(value, "reason")
