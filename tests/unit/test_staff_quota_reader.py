"""Quota read visibility: known only for claude/codex snapshots; else unknown (#4278 J-12)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from staff import quota
from staff.quota_reader import credential_probe_quota, read_status

NOW = datetime(2026, 9, 26, 18, 0, tzinfo=UTC)

CLAUDE_EVENT = {
    "type": "rate_limit_event",
    "rate_limit_info": {
        "status": "allowed",
        "resetsAt": 1790454000,
        "rateLimitType": "five_hour",
        "utilization": 0.5,
    },
}


@pytest.mark.unit
def test_read_status_unknown_for_unreadable_providers() -> None:
    assert read_status("gemini", NOW) == "unknown"
    assert read_status("antigravity", NOW) == "unknown"
    assert read_status("grok", NOW) == "unknown"


@pytest.mark.unit
def test_read_status_unknown_without_snapshot(tmp_path: Path) -> None:
    store = quota.QuotaStore(tmp_path / "q.json")
    assert read_status("claude", NOW, store=store) == "unknown"
    assert read_status("codex", NOW, store=store, codex_dirs=[]) == "unknown"


@pytest.mark.unit
def test_read_status_known_when_snapshot_exists(tmp_path: Path) -> None:
    store = quota.QuotaStore(tmp_path / "q.json")
    snap = quota.from_claude_event(CLAUDE_EVENT, observed_at=NOW)
    assert snap is not None
    store.record(snap)
    assert read_status("claude", NOW, store=store) == "known"


@pytest.mark.unit
def test_credential_probe_never_reports_available_for_unreadable_provider() -> None:
    status, blocked, detail = credential_probe_quota("antigravity")
    assert status == "unknown"
    assert blocked is None
    assert "not readable" in detail


@pytest.mark.unit
def test_credential_probe_unknown_for_readable_provider_without_data(tmp_path: Path) -> None:
    store = quota.QuotaStore(tmp_path / "q.json")
    status, blocked, _ = credential_probe_quota("claude", store=store, now=NOW)
    assert status == "unknown"
    assert blocked is None


@pytest.mark.unit
def test_credential_probe_blocks_when_known_snapshot_over_ceiling(tmp_path: Path) -> None:
    store = quota.QuotaStore(tmp_path / "q.json")
    snap = quota.QuotaSnapshot(
        account="claude",
        observed_at=NOW,
        source="test",
        windows=(quota.QuotaWindow("five_hour", 99.0),),
    )
    store.record(snap)
    status, blocked, reason = credential_probe_quota("claude", ceiling=85.0, store=store, now=NOW)
    assert status == "known"
    assert blocked is True
    assert "five_hour" in reason


@pytest.mark.unit
def test_quota_report_includes_quota_status_never_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_QUOTA_STATE", str(tmp_path / "q.json"))
    body = quota.report(["claude", "gemini", "antigravity"], NOW, store=quota.QuotaStore(tmp_path / "q.json"))
    rows = {row["provider"]: row for row in body["providers"]}
    assert rows["claude"]["quota_status"] == "unknown"
    assert rows["gemini"]["quota_status"] == "unknown"
    assert rows["antigravity"]["quota_status"] == "unknown"
    for row in body["providers"]:
        assert row["quota_status"] in ("known", "unknown")
        assert row["quota_status"] != "available"
