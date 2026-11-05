"""Tests for backend/fleet_backup_drill.py (#4020)."""

from __future__ import annotations

from unittest.mock import patch

import fleet_backup_drill as fbd


def test_emit_restore_drill_failure_records_event_and_notifies() -> None:
    events: list[object] = []
    pushes: list[tuple[str, str, str]] = []

    class _Store:
        def record(self, event: object) -> None:
            events.append(event)

    with (
        patch("fleet_backup_drill.get_event_store", return_value=_Store()),
        patch(
            "fleet_backup_drill.push.notify",
            side_effect=lambda channel, title, detail: pushes.append((channel, title, detail)),
        ),
    ):
        fbd.emit_restore_drill_failure_alert("node-b", "verification failed")

    assert len(events) == 1
    assert getattr(events[0], "kind") == "backup_drill_failure"
    assert getattr(events[0], "node") == "node-b"
    assert pushes[0][0] == "staff.escalation"
