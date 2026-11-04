"""Hub ingest for failed fleet backup restore drills (#4020)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import push
from fleet_events import FleetEvent, get_event_store

log = logging.getLogger("dashboard.fleet_backup_drill")


def emit_restore_drill_failure_alert(machine: str, detail: str) -> None:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    title = f"Fleet backup restore drill failed on {machine}"
    get_event_store().record(
        FleetEvent(
            ts=now_ms,
            severity="critical",
            kind="backup_drill_failure",
            title=title,
            detail=detail,
            node=machine,
        )
    )
    push.notify("staff.escalation", title, detail)
    log.warning("fleet backup drill failure on %s", machine)
