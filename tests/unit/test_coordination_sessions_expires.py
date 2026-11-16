"""Merged coordination sessions drop expired staff-derived rows (#4458 fix-forward)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from coordination import hub_sessions, service


@pytest.fixture(autouse=True)
def _reset_hub() -> None:
    hub_sessions.reset_hub_sessions()


def test_expired_staff_run_session_row_is_omitted_from_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")

    async def staff_with_expired(repo=None):
        return ([{"id": "gone", "repo": "Tools", "expires": expired, "started_at": expired}], [])

    monkeypatch.setattr(
        service.board,
        "read_sessions",
        lambda repo=None: {
            "available": True,
            "complete": True,
            "sessions": [],
            "messages": [],
            "conflicts": [],
            "warnings": [],
        },
    )
    monkeypatch.setattr(service, "staff_runs", staff_with_expired)
    result = asyncio.run(service.sessions(None))
    assert not any(s.get("session") == "staff:gone" for s in result["sessions"])


def test_long_running_staff_run_without_expires_stays_in_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """The synthesized started_at + TTL expiry must not hide a run that is still active."""
    started = (datetime.now(UTC) - timedelta(hours=9)).isoformat().replace("+00:00", "Z")

    async def staff_long_running(repo=None):
        return ([{"id": "long", "repo": "Tools", "status": "running", "started_at": started}], [])

    monkeypatch.setattr(
        service.board,
        "read_sessions",
        lambda repo=None: {
            "available": True,
            "complete": True,
            "sessions": [],
            "messages": [],
            "conflicts": [],
            "warnings": [],
        },
    )
    monkeypatch.setattr(service, "staff_runs", staff_long_running)
    result = asyncio.run(service.sessions(None))
    assert any(s.get("session") == "staff:long" for s in result["sessions"])
