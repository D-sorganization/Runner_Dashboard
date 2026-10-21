"""Typed contracts for node auto-deploy (Design by Contract)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

DeployResult = Literal["success", "rollback", "skipped", "failed", "in_progress"]
DeployScope = Literal["system", "user"]


@dataclass(frozen=True)
class AutoDeployEvent:
    """One append-only deploy/rollback record exposed to fleet status."""

    at: str
    commit: str
    result: DeployResult
    message: str
    rolled_back_from: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "at": self.at,
            "commit": self.commit,
            "result": self.result,
            "message": self.message,
        }
        if self.rolled_back_from:
            out["rolled_back_from"] = self.rolled_back_from
        return out


@dataclass
class AutoDeployState:
    """Persisted node-local auto-deploy status (bounded event ring)."""

    last_attempt_at: str | None = None
    last_result: DeployResult | None = None
    target_commit: str | None = None
    previous_commit: str | None = None
    message: str | None = None
    attention: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_attempt_at": self.last_attempt_at,
            "last_result": self.last_result,
            "target_commit": self.target_commit,
            "previous_commit": self.previous_commit,
            "message": self.message,
            "attention": self.attention,
            "events": list(self.events),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AutoDeployState:
        events = payload.get("events")
        if not isinstance(events, list):
            events = []
        return cls(
            last_attempt_at=payload.get("last_attempt_at"),
            last_result=payload.get("last_result"),
            target_commit=payload.get("target_commit"),
            previous_commit=payload.get("previous_commit"),
            message=payload.get("message"),
            attention=bool(payload.get("attention")),
            events=[e for e in events if isinstance(e, dict)],
        )
