"""Outcome shared by conversational turns and their post-turn continuity writer."""

from dataclasses import dataclass, field
from typing import Any

from staff.reply_contract import ProposedAction


@dataclass
class ChatTurnResult:
    """Outcome of a conversational chat turn."""

    ok: bool
    reply: str = ""
    session_id: str | None = None
    handoff: str | None = None
    question: str | None = None
    actions: list[ProposedAction] = field(default_factory=list)
    failure_class: str | None = None
    retryable: bool = False
    remediation: str = ""
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    replayed_history: bool = False
    memory_digest: dict[str, Any] | None = None
