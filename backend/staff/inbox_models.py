"""Staff inbox payload models.

Split from ``staff.inbox`` to keep that module under the repo's 500-line soft
cap and to avoid a circular import for the auth sign-in collector
(``staff.inbox_auth``), which constructs these dataclasses.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

InboxSource = Literal[
    "approval",
    "needs_input",
    "escalation",
    "project_decision",
    "board_proposal",
    "auth_sign_in",
]


@dataclass(frozen=True)
class InboxItem:
    """A single item requiring owner/operator attention."""

    id: str
    source: InboxSource
    title: str
    summary: str
    severity: Literal["low", "medium", "high", "critical"]
    created_at: str
    link: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # Decision SLA (WP-2.6, #1607): when the owner must decide, and what Barb applies if silent.
    decide_by: str | None = None
    default_if_silent: str = ""
    details: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceStatus:
    """Status of an individual inbox aggregation source."""

    status: Literal["ok", "unavailable"]
    count: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InboxAggregate:
    """Full aggregated inbox payload."""

    items: list[InboxItem]
    counts: dict[str, int]
    sources: dict[str, SourceStatus]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        item_dicts = [item.to_dict() for item in self.items]
        return {
            "items": item_dicts,
            "inbox": item_dicts,
            "count": self.counts["total"],
            "counts": self.counts,
            "sources": {k: v.to_dict() for k, v in self.sources.items()},
            "generated_at": self.generated_at,
        }
