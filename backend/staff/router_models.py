"""Data models and constants for Barb request routing (SC-C2, Issue #1315)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DEFAULT_CONFIDENCE_THRESHOLD = 0.65

RE_AT_MENTION = re.compile(r"(?:^|\s)@([a-zA-Z0-9_-]+)", re.IGNORECASE)
RE_ROLE_COMMAND = re.compile(r"^\s*/role\s+([a-zA-Z0-9_-]+)", re.IGNORECASE)

BARB_DIRECT_KEYWORDS: tuple[str, ...] = (
    "directive",
    "directives",
    "priority",
    "priorities",
    "waiting-on-dieter",
    "waiting on dieter",
    "portfolio status",
    "fleet status",
    "morning brief",
    "evening brief",
    "what is blocked",
    "who owns what",
    "what is underway",
    "fleet in-flight",
)

ROLE_KEYWORD_RULES: dict[str, tuple[str, ...]] = {
    "issue-remediator": (
        "issue #",
        "fix issue",
        "resolve issue",
        "bug in issue",
        "broken test in issue",
        "close issue",
    ),
    "pr-remediator": (
        "pr #",
        "review pr",
        "fix pr",
        "pull request #",
        "merge conflict",
        "ci failure",
        "failing check",
    ),
    "cartographer": (
        "architecture map",
        "architecture maps",
        "contract seam",
        "contract seams",
        "dependency graph",
        "where is",
        "codebase question",
        "codebase map",
        "codebase search",
        "ask codebase",
        "locate code",
        "where is handled",
    ),
    "sanitation": (
        "clean up stale",
        "stale branch",
        "stale branches",
        "orphaned worktrees",
        "unused worktrees",
        "temp files",
    ),
    "librarian": (
        "documentation",
        "docs/",
        "style guide",
        "doc audit",
        "update readme",
        "readme.md",
        "codebase docs",
        "explain endpoint",
        "what does",
        "how does",
    ),
    "maintenance": (
        "runner offline",
        "restart runner",
        "runner is offline",
        "take offline",
        "bring online",
        "diagnose runner",
        "diagnose machine",
        "stalled job",
        "stalled jobs",
        "compact disk",
        "vhdx",
        "scale runner",
        "node down",
        "node up",
    ),
    "night-watch": (
        "nightly scan",
        "hygiene audit",
        "night watch",
        "test fix sweep",
    ),
    "project-steward": (
        "repo charter",
        "roadmap status",
        "deferred backlog",
        "charter",
    ),
    "fleet-critic": (
        "architectural review",
        "code critique",
        "design review",
        "code smell",
    ),
    "pragmatic-programmer": (
        "refactor",
        "code health",
        "type hints",
        "mypy compliance",
        "lint compliance",
        "implement",
        "endpoint",
        "new feature",
        "write code",
    ),
    "research-scout": (
        "academic paper",
        "scientific literature",
        "arxiv",
        "upstream repo study",
    ),
    "oss-scout": (
        "open source license",
        "oss license",
        "dependency license",
        "third-party license",
    ),
    "board-secretary": (
        "board meeting",
        "meeting agenda",
        "board proposal",
        "meeting minutes",
    ),
    "maxwell": (
        "maxwell daemon",
        "maxwell status",
        "maxwell task",
        "local tools",
        "desktop agent",
        "daemon health",
    ),
}

CODE_CHANGE_KEYWORDS: tuple[str, ...] = (
    "implement",
    "fix bug",
    "open a pr",
    "create pr",
    "refactor",
    "write code",
    "new endpoint",
    "add feature",
    "modify backend",
    "modify frontend",
)


def detect_code_change(text: str) -> bool:
    """Return True if prompt suggests implementing or modifying code."""
    low = text.lower()
    return any(kw in low for kw in CODE_CHANGE_KEYWORDS)


@dataclass(frozen=True)
class RoutingDecision:
    """Record of a Barb routing choice with confidence and rationale."""

    chosen_role: str | None
    confidence: float
    reason: str
    alternatives: tuple[str, ...] = ()
    mode: str = "deterministic"
    needs_clarification: bool = False
    clarifying_question: str | None = None
    is_code_change: bool = False

    @property
    def handoff_body(self) -> str:
        target = (self.chosen_role or "unknown").replace("-", " ").title()
        return f"Barb → {target}: {self.reason}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "chosen_role": self.chosen_role,
            "confidence": self.confidence,
            "reason": self.reason,
            "alternatives": list(self.alternatives),
            "mode": self.mode,
            "needs_clarification": self.needs_clarification,
            "clarifying_question": self.clarifying_question,
            "is_code_change": self.is_code_change,
            "handoff_body": self.handoff_body,
        }


@dataclass(frozen=True)
class HandoffResult:
    """Outcome of executing a handoff to a destination role."""

    success: bool
    target_role: str
    target_thread_id: str
    handoff_message_id: str
    work_item_id: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "target_role": self.target_role,
            "target_thread_id": self.target_thread_id,
            "handoff_message_id": self.handoff_message_id,
            "work_item_id": self.work_item_id,
            "error": self.error,
        }


@dataclass(frozen=True)
class RoutingFeedbackRecord:
    """Auditable feedback record captured when a user overrides Barb's routing."""

    id: str
    original_role: str
    override_role: str
    prompt: str
    reason: str
    overridden_by: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "original_role": self.original_role,
            "override_role": self.override_role,
            "prompt": self.prompt,
            "reason": self.reason,
            "overridden_by": self.overridden_by,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class RoutingOverrideRecord:
    """Result of executing a routing override on a handoff message."""

    success: bool
    original_role: str
    new_target_role: str
    handoff_message_id: str
    work_item_id: str | None = None
    new_thread_id: str | None = None
    feedback_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "original_role": self.original_role,
            "new_target_role": self.new_target_role,
            "handoff_message_id": self.handoff_message_id,
            "work_item_id": self.work_item_id,
            "new_thread_id": self.new_thread_id,
            "feedback_id": self.feedback_id,
        }
