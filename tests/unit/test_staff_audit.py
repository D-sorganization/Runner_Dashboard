"""Unit tests for durable append-only staff audit log (SC-A8, Issue #1298)."""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.staff.audit import (
    ALLOWED_ACTIONS,
    ALLOWED_SURFACES,
    AuditError,
    StaffAuditRecord,
    StaffAuditStore,
    archive_old_audit_entries,
    export_audit_csv,
    export_audit_ndjson,
    reset_audit_store,
)


@pytest.fixture
def audit_db(tmp_path: Path) -> Iterator[Path]:
    """Provide a clean SQLite database path for audit tests."""
    db_file = tmp_path / "test_staff_audit.sqlite3"
    reset_audit_store()
    yield db_file
    reset_audit_store()


def test_audit_table_created_and_schema_matches(audit_db: Path) -> None:
    """The staff_audit table exists with required columns and indexes."""
    store = StaffAuditStore(audit_db)
    cols = store.columns()
    expected = {
        "id",
        "ts",
        "principal",
        "on_behalf_of",
        "surface",
        "action",
        "target",
        "request_id",
        "thread_id",
        "run_id",
        "outcome",
        "detail",
    }
    assert expected.issubset(cols)
    store.close()


def test_record_audit_and_persistence_across_restart(audit_db: Path) -> None:
    """Each write point produces exactly one row and restart does not lose rows."""
    store = StaffAuditStore(audit_db)
    rec = StaffAuditRecord(
        principal="operator:alice",
        on_behalf_of="team-core",
        surface="ui",
        action="dispatch",
        target="role:night-watch",
        request_id="req-123",
        thread_id="th-abc",
        run_id="run-789",
        outcome="success",
        detail={"repo": "Tools", "branch": "fix/123"},
    )
    audit_id = store.record(rec)
    assert audit_id > 0
    store.close()

    # Reopen database to verify durable persistence
    reopened = StaffAuditStore(audit_db)
    rows = reopened.list_entries(limit=10)
    assert len(rows) == 1
    loaded = rows[0]
    assert loaded.id == audit_id
    assert loaded.principal == "operator:alice"
    assert loaded.on_behalf_of == "team-core"
    assert loaded.surface == "ui"
    assert loaded.action == "dispatch"
    assert loaded.target == "role:night-watch"
    assert loaded.request_id == "req-123"
    assert loaded.thread_id == "th-abc"
    assert loaded.run_id == "run-789"
    assert loaded.outcome == "success"
    assert loaded.detail == {"repo": "Tools", "branch": "fix/123"}
    reopened.close()


def test_allowed_surfaces_and_actions() -> None:
    """Enforce vocabulary of surfaces and actions defined in SC-A8."""
    assert "ui" in ALLOWED_SURFACES
    assert "api" in ALLOWED_SURFACES
    assert "mcp" in ALLOWED_SURFACES
    assert "barb" in ALLOWED_SURFACES
    assert "scheduler" in ALLOWED_SURFACES

    expected_actions = {
        "dispatch",
        "cancel",
        "hold_set",
        "hold_clear",
        "schedule_toggle",
        "proposal_create",
        "proposal_approve",
        "proposal_deny",
        "proposal_execute",
        "maintenance",
        "routing",
    }
    assert expected_actions.issubset(ALLOWED_ACTIONS)


def test_list_entries_filtering_and_pagination(audit_db: Path) -> None:
    """The endpoint/store filters by principal, thread_id, action, surface, and paginates."""
    store = StaffAuditStore(audit_db)
    for i in range(15):
        store.record(
            StaffAuditRecord(
                ts=f"2026-09-24T10:{i:02d}:00Z",
                principal="user-a" if i % 2 == 0 else "user-b",
                thread_id="th-1" if i < 10 else "th-2",
                action="dispatch" if i % 3 == 0 else "cancel",
                surface="mcp" if i % 2 == 0 else "barb",
                target=f"role-{i}",
                outcome="success",
            )
        )

    # Filter by principal
    user_a_entries = store.list_entries(principal="user-a")
    assert all(e.principal == "user-a" for e in user_a_entries)
    assert len(user_a_entries) == 8

    # Filter by thread_id
    th2_entries = store.list_entries(thread_id="th-2")
    assert len(th2_entries) == 5
    assert all(e.thread_id == "th-2" for e in th2_entries)

    # Filter by action
    cancel_entries = store.list_entries(action="cancel")
    assert all(e.action == "cancel" for e in cancel_entries)

    # Pagination: limit & offset
    page1 = store.list_entries(limit=5, offset=0)
    page2 = store.list_entries(limit=5, offset=5)
    assert len(page1) == 5
    assert len(page2) == 5
    assert page1[0].id != page2[0].id

    # Total count matching filters
    assert store.count_entries(principal="user-b") == 7
    store.close()


def test_fail_closed_on_mutating_actions_and_logs_loudly_on_read_only(audit_db: Path) -> None:
    """An audit write failure fails mutating action closed, logs loudly for read-only."""
    store = StaffAuditStore(audit_db)
    # Simulate DB failure by closing connection
    store.close()

    # Mutating / maintenance action fails closed (raises AuditError)
    rec_mutating = StaffAuditRecord(
        principal="operator:bob",
        action="maintenance",
        surface="api",
        target="worktrees:cleanup",
        outcome="success",
    )
    with pytest.raises(AuditError):
        store.record(rec_mutating, fail_closed=True)

    # Read-only action logs loudly without raising
    rec_readonly = StaffAuditRecord(
        principal="operator:bob",
        action="routing",
        surface="barb",
        target="eval:role_check",
        outcome="success",
    )
    # Should not raise
    audit_id = store.record(rec_readonly, fail_closed=False)
    assert audit_id == -1


def test_retention_archive_to_gzip(audit_db: Path, tmp_path: Path) -> None:
    """Retention: keep 180 days, then archive to gzip file; never delete in place."""
    store = StaffAuditStore(audit_db)
    archive_dir = tmp_path / "archives"

    # Insert old entries (beyond 180 days: before 2026-03-28)
    old_ts = "2025-10-01T12:00:00Z"
    for i in range(5):
        store.record(
            StaffAuditRecord(
                ts=old_ts,
                principal="old-agent",
                action="dispatch",
                surface="scheduler",
                target=f"role-old-{i}",
                outcome="success",
                detail={"index": i},
            )
        )

    # Insert recent entries
    recent_ts = "2026-09-24T12:00:00Z"
    for i in range(3):
        store.record(
            StaffAuditRecord(
                ts=recent_ts,
                principal="recent-agent",
                action="dispatch",
                surface="ui",
                target=f"role-recent-{i}",
                outcome="success",
                detail={"index": i},
            )
        )

    assert store.count_entries() == 8

    # Run archive with retention 180 days relative to 2026-09-24
    archived_count, archive_files = archive_old_audit_entries(
        store=store,
        retention_days=180,
        archive_dir=archive_dir,
        reference_date="2026-09-24T12:00:00Z",
    )
    assert archived_count == 5
    assert len(archive_files) == 1
    archive_path = archive_files[0]
    assert archive_path.exists()
    assert archive_path.name.endswith(".jsonl.gz")

    # Verify gzip contents
    with gzip.open(archive_path, "rt", encoding="utf-8") as gz:
        lines = [json.loads(line) for line in gz if line.strip()]
    assert len(lines) == 5
    assert lines[0]["principal"] == "old-agent"

    # Verify database: only recent entries remain
    remaining = store.list_entries(limit=10)
    assert len(remaining) == 3
    assert all(r.principal == "recent-agent" for r in remaining)
    store.close()


def test_export_csv_and_ndjson(audit_db: Path) -> None:
    """Format audit entries as valid CSV and NDJSON exports."""
    store = StaffAuditStore(audit_db)
    rec1 = StaffAuditRecord(
        ts="2026-09-24T10:00:00Z",
        principal="operator:charlie",
        surface="ui",
        action="hold_set",
        target="repo:Tools",
        outcome="success",
        detail={"reason": "Testing hold"},
    )
    rec2 = StaffAuditRecord(
        ts="2026-09-24T10:05:00Z",
        principal="bot:barb",
        surface="barb",
        action="cancel",
        target="run:run-456",
        outcome="success",
        detail={},
    )
    store.record(rec1)
    store.record(rec2)

    records = store.list_entries(limit=10)

    # CSV Export
    csv_text = export_audit_csv(records)
    lines = csv_text.strip().splitlines()
    assert len(lines) == 3  # Header + 2 data rows
    assert "ts,principal,on_behalf_of,surface,action,target" in lines[0]
    assert "operator:charlie" in csv_text
    assert "bot:barb" in csv_text

    # NDJSON Export
    ndjson_text = export_audit_ndjson(records)
    ndjson_lines = [json.loads(line) for line in ndjson_text.strip().splitlines()]
    assert len(ndjson_lines) == 2
    assert ndjson_lines[0]["principal"] in ("operator:charlie", "bot:barb")
    store.close()
