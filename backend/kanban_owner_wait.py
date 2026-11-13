"""Kanban owner-wait ingestion: holds, owner labels, pending approvals (#4321).

Read-only projection inputs for ``KanbanOwnerWaitSnapshot``; never mutates holds.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from coordination.job_filters import gate_reason
from kanban_projection import KanbanOwnerWaitSnapshot
from staff.holds import FAIL_CLOSED_HOLD_ID, Hold, HoldsList, holds_path

log = logging.getLogger("dashboard.kanban.owner_wait")

OWNER_WAIT_LABELS = frozenset(
    {
        "panel-needs-owner",
        "blocked",
        "do-not-automate",
        "do-not-merge",
        "judgement:contested",
    }
)

HOLD_LABEL_PREFIX = "hold:"


@dataclass(frozen=True, slots=True)
class HoldsSyncState:
    """Last-known holds file metadata for kanban freshness display."""

    readable: bool
    last_sync_at: str | None
    holds_url: str


def _staff_holds_console_url() -> str:
    return "/staff?tab=holds"


def holds_sync_state(holds_list: HoldsList | None = None) -> HoldsSyncState:
    """Whether schedule holds are trustworthy and when the list was last written."""
    lst = holds_list or HoldsList()
    path = lst.path
    last_sync: str | None = None
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                stamp = raw.get("updated_at")
                if stamp:
                    last_sync = str(stamp)
    except (OSError, ValueError, TypeError) as exc:
        log.warning("kanban: could not read holds updated_at from %s: %s", path, exc)
    loaded = lst.load()
    readable = not any(h.id == FAIL_CLOSED_HOLD_ID for h in loaded)
    return HoldsSyncState(readable=readable, last_sync_at=last_sync, holds_url=_staff_holds_console_url())


def _owner_label_reasons(labels: list[str]) -> list[str]:
    reasons: list[str] = []
    for name in labels:
        lowered = name.strip().lower()
        if lowered.startswith(HOLD_LABEL_PREFIX) or lowered in OWNER_WAIT_LABELS:
            reasons.append(f"owner label {name!r}")
    return reasons


def build_owner_wait_snapshot(
    *,
    issue_labels: list[str],
    scoped_holds: list[Hold],
    approval_reason: str | None = None,
    detail_url: str | None = None,
) -> KanbanOwnerWaitSnapshot:
    """Compose ``owner_wait`` for one work item from labels, schedule holds, and approvals."""
    lowered = {name.strip().lower() for name in issue_labels}
    reasons: list[str] = []
    hold_hit = gate_reason(lowered, scoped_holds)
    if hold_hit:
        reasons.append(hold_hit)
    reasons.extend(_owner_label_reasons(issue_labels))
    if approval_reason:
        reasons.append(approval_reason)
    waiting = bool(reasons)
    return KanbanOwnerWaitSnapshot(
        available=True,
        waiting=waiting,
        reasons=tuple(reasons),
        html_url=detail_url or _staff_holds_console_url(),
    )


def hold_entry_link(hold: Hold) -> str:
    """Stable operator link for a schedule hold card."""
    return f"{_staff_holds_console_url()}#{hold.id}"


def holds_file_path() -> Path:
    return holds_path()
