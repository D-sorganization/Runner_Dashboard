"""Classify provider stop-hook failures so panel seats never store them as answers (#2899)."""

from __future__ import annotations

import re

__all__ = ["hook_error_reason", "is_hook_error_reply"]

_HOOK_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"can'?t discover a git worktree", re.IGNORECASE),
    re.compile(r"cannot discover a git worktree", re.IGNORECASE),
    re.compile(r"^Agent hook checks failed before handoff:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"Stop hook failed", re.IGNORECASE),
    re.compile(r"trusted_agent_hook(?:\.py)?", re.IGNORECASE),
)


def is_hook_error_reply(text: str) -> bool:
    """Return True when ``text`` is a stop-hook failure rather than a panelist turn.

    Pre: ``text`` is the provider's final reply body (may be empty).
    Post: never raises; normal panel prose that merely mentions worktrees stays False.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    return any(pattern.search(stripped) for pattern in _HOOK_ERROR_PATTERNS)


def hook_error_reason(text: str) -> str:
    """Normalise a hook failure for ``TurnOutcome.error``.

    Pre: :func:`is_hook_error_reply` is True for ``text``.
    Post: returns a short, operator-facing reason prefixed with ``provider stop-hook error``.
    """
    assert is_hook_error_reply(text), "hook_error_reason requires a hook-error reply"  # noqa: S101
    excerpt = (text or "").strip().replace("\n", " ")[:400]
    return f"provider stop-hook error: {excerpt}"
