"""Intake proposal records (distinct from board /api/proposals)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class IntakeProposal:
    id: str
    state: str
    subject: str
    body_plain: str
    sender: str
    message_id: str
    dispatch: dict[str, Any]
    created_at: datetime
    decided_by: str = ""
    decision_reason: str = ""
    owner_approval_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "subject": self.subject,
            "body_plain": self.body_plain,
            "sender": self.sender,
            "message_id": self.message_id,
            "dispatch": self.dispatch,
            "created_at": self.created_at.isoformat(),
            "decided_by": self.decided_by,
            "decision_reason": self.decision_reason,
            "owner_approval_ref": self.owner_approval_ref,
        }
