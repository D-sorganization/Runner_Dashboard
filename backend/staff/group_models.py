"""Data models and constants for staff groups and multi-seat coordination (SC-B9, Issue #1339)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from staff.pricing import PRICE_TABLE, Price
from staff.reply_contract import ProposedAction

DEFAULT_COST_THRESHOLD_USD = 0.50
DEFAULT_SEAT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class SeatSpec:
    """Specification of a member seat within a staff group."""

    name: str
    title: str
    role: str
    provider: str
    model: str
    mandate: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "mandate": self.mandate,
        }


@dataclass(frozen=True)
class GroupDefinition:
    """Specification of a staff group (e.g. Board of Directors)."""

    id: str
    name: str
    coordinator: str
    seats: tuple[SeatSpec, ...]
    description: str = ""
    cost_threshold_usd: float = DEFAULT_COST_THRESHOLD_USD

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "coordinator": self.coordinator,
            "seats": [s.to_dict() for s in self.seats],
            "description": self.description,
            "cost_threshold_usd": self.cost_threshold_usd,
        }


@dataclass
class SeatReply:
    """Individual seat reply within a group turn."""

    seat_name: str
    status: str = "ok"  # "ok", "timeout", "error"
    text: str = ""
    error_detail: str = ""
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat_name": self.seat_name,
            "status": self.status,
            "text": self.text,
            "error_detail": self.error_detail,
            "cost_usd": round(self.cost_usd, 4),
        }


@dataclass
class GroupCostEstimate:
    """Cost breakdown for a group chat turn."""

    group_id: str
    total_cost_usd: float
    cost_per_seat: dict[str, float]
    exceeds_threshold: bool
    threshold_usd: float
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "cost_per_seat": {k: round(v, 4) for k, v in self.cost_per_seat.items()},
            "exceeds_threshold": self.exceeds_threshold,
            "threshold_usd": round(self.threshold_usd, 4),
            "warning": self.warning,
        }


@dataclass
class ConsensusResult:
    """Result of collating seat replies and synthesizing consensus."""

    group_id: str
    coordinator: str
    quorum: str
    summary: str
    seat_replies: list[SeatReply]
    proposed_actions: list[ProposedAction] = field(default_factory=list)
    total_cost_usd: float = 0.0


BOARD_SEATS: tuple[SeatSpec, ...] = (
    SeatSpec(
        name="alpha",
        title="Alpha (Architecture)",
        role="board-seat-alpha",
        provider="claude",
        model="opus-5",
        mandate="Cross-repo architectural review",
    ),
    SeatSpec(
        name="bravo",
        title="Bravo (Science)",
        role="board-seat-bravo",
        provider="gemini",
        model="gemini-2.5-pro",
        mandate="Scientific validity, measurement rigor",
    ),
    SeatSpec(
        name="charlie",
        title="Charlie (Cadence)",
        role="board-seat-charlie",
        provider="codex",
        model="gpt-5",
        mandate="Execution cadence, CI/CD health",
    ),
    SeatSpec(
        name="delta",
        title="Delta (Docs & UX)",
        role="board-seat-delta",
        provider="claude",
        model="sonnet-5",
        mandate="Documentation, user experience, API surface",
    ),
)


def get_group_threshold() -> float:
    try:
        return float(os.environ.get("GROUP_COST_GUARD_THRESHOLD_USD", str(DEFAULT_COST_THRESHOLD_USD)))
    except ValueError:
        return DEFAULT_COST_THRESHOLD_USD


def lookup_seat_price(provider: str, model: str) -> Price:
    prov_dict = PRICE_TABLE.get(provider.lower(), {})
    for k, price in prov_dict.items():
        if k in model.lower():
            return price
    return Price(input_usd=2.5, output_usd=10.0, estimate=True)
