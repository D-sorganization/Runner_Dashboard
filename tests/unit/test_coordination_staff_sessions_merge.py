"""Staff runs and hub-registered lanes surface in coordination sessions (#4458)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from coordination import hub_sessions, service
from coordination.staff_view import staff_run_as_session


def test_staff_run_maps_to_session_shape() -> None:
    row = staff_run_as_session(
        {
            "id": "run-abc",
            "role": "code-reviewer",
            "provider": "grok",
            "machine": "Desk",
            "repo": "Runner_Dashboard",
            "target": "#99",
            "status": "running",
            "started_at": "2026-11-15T00:00:00Z",
        }
    )
    assert row["session"] == "staff:run-abc"
    assert row["source"] == "staff"
    assert row["role"] == "code-reviewer"
    assert row["provider"] == "grok"
    assert row["machine"] == "Desk"
    assert row["issue"] == 99
    assert row["expires"]


def test_hub_session_register_and_list() -> None:
    hub_sessions.reset_hub_sessions()
    expires = (datetime.now(UTC) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    hub_sessions.upsert(
        {
            "session": "cursor-lane-1",
            "role": "cursor",
            "provider": "cursor",
            "machine": "cloud",
            "repo": "Tools",
            "target": "issue:7",
            "expires": expires,
        }
    )
    rows = hub_sessions.list_sessions()
    assert len(rows) == 1 and rows[0]["session"] == "cursor-lane-1"


async def test_sessions_merges_staff_runs_into_sessions(monkeypatch) -> None:
    board = {
        "available": True,
        "complete": True,
        "sessions": [{"session": "rm-1", "agent": "codex", "repo": "Tools", "source": "board"}],
        "messages": [],
        "conflicts": [],
        "warnings": [],
    }
    monkeypatch.setattr(service.board, "read_sessions", lambda repo=None: board)

    async def staff(repo=None):
        return (
            [
                {
                    "id": "run-1",
                    "role": "ad-hoc",
                    "provider": "claude",
                    "machine": "node-b",
                    "repo": "Tools",
                    "target": "",
                    "status": "running",
                    "started_at": "2026-11-15T01:00:00Z",
                }
            ],
            [],
        )

    monkeypatch.setattr(service, "staff_runs", staff)
    hub_sessions.reset_hub_sessions()
    out = await service.sessions(None)
    session_ids = {s["session"] for s in out["sessions"]}
    assert "rm-1" in session_ids
    assert "staff:run-1" in session_ids
    staff_row = next(s for s in out["sessions"] if s["session"] == "staff:run-1")
    assert staff_row["machine"] == "node-b" and staff_row["provider"] == "claude"