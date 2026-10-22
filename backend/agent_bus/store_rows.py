"""Shared row types and inbox message shaping for the agent bus store (#2155)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

BARB_ROLE = "barb"


def iso_after(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class PrincipalRow:
    session_id: str
    role: str
    provider: str
    node: str
    principal_id: str
    lease_expires_at: str

    def live(self, now: datetime | None = None) -> bool:
        when = now or datetime.now(UTC)
        try:
            exp = datetime.fromisoformat(self.lease_expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        return exp > when


def message_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["message_id"],
        "cursor": row["cursor"],
        "from_session": row["from_session"],
        "to": row["to_address"],
        "type": row["msg_type"],
        "repo": row["repo"],
        "work_kind": row["work_kind"],
        "work_number": row["work_number"],
        "body": row["body_md"],
        "thread_id": row["thread_id"],
        "thread_message_id": row["thread_message_id"],
        "created_at": row["created_at"],
        "acked_at": row["acked_at"],
    }
