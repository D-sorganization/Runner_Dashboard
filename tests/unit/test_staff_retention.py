"""Unit tests for staff data retention archival and thread export (Issue #1490).

Tests:
1. archive_old_conversations archives inactive/closed threads and their messages to gzip.
2. archive_old_proposals archives terminal proposals older than 180 days to gzip.
3. archive_old_runs archives terminal runs and events older than 180 days to gzip.
4. archive_old_work_items archives terminal work items and events older than 180 days to gzip.
5. run_retention_archival orchestrates archival across all entities.
6. export_thread_markdown and export_thread_json formatting.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from staff.conversation_models import MessageRecord, ThreadRecord
from staff.conversations import ConversationStore
from staff.retention import (
    archive_old_conversations,
    archive_old_proposals,
    archive_old_runs,
    archive_old_work_items,
    export_thread_json,
    export_thread_markdown,
    run_retention_archival,
)
from staff.store import RunRecord, RunStore
from staff.work_items import WorkItemStore


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    return tmp_path / "staff_runs.sqlite3"


@pytest.fixture
def archive_dir(tmp_path: Path) -> Path:
    d = tmp_path / "archive"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.mark.unit
def test_archive_old_conversations(temp_db: Path, archive_dir: Path) -> None:
    """Conversations inactive for > 180 days are gzipped and deleted from SQLite."""
    store = ConversationStore(temp_db)

    # 1. Old thread (updated 200 days before reference date 2026-09-26)
    old_thread = store.create_thread(
        title="Old archived discussion",
        kind="direct",
        participants=["barb", "user"],
        meta={"topic": "ancient"},
    )
    msg1 = store.add_message(
        thread_id=old_thread.id,
        author_kind="user",
        author="user",
        kind="text",
        body_md="Hello from January",
    )
    # Manually backdate old thread and messages after message creation
    old_ts = "2026-01-01T12:00:00Z"
    with store._lock:  # noqa: SLF001
        store._conn.execute(  # noqa: SLF001
            "UPDATE threads SET created_at = ?, updated_at = ?, status = 'archived' WHERE id = ?",
            (old_ts, old_ts, old_thread.id),
        )
        store._conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (old_ts, msg1.id))  # noqa: SLF001

    # 2. Recent thread (updated 10 days ago)
    recent_thread = store.create_thread(
        title="Recent active discussion",
        kind="direct",
        participants=["barb", "user"],
    )
    store.add_message(
        thread_id=recent_thread.id,
        author_kind="user",
        author="user",
        kind="text",
        body_md="Hello from September",
    )

    # Run archive with retention 180 days relative to 2026-09-26T12:00:00Z
    archived_count, archive_files = archive_old_conversations(
        store=store,
        retention_days=180,
        archive_dir=archive_dir,
        reference_date="2026-09-26T12:00:00Z",
    )

    assert archived_count == 1
    assert len(archive_files) == 1
    archive_file = archive_files[0]
    assert archive_file.exists()
    assert "conversations_archive_" in archive_file.name

    # Inspect gzip contents
    with gzip.open(archive_file, "rt", encoding="utf-8") as gz:
        lines = [json.loads(line) for line in gz if line.strip()]
    assert len(lines) == 1
    assert lines[0]["thread"]["id"] == old_thread.id
    assert len(lines[0]["messages"]) == 1
    assert lines[0]["messages"][0]["body_md"] == "Hello from January"

    # Database verification: old thread removed, recent thread remains
    assert store.get_thread(old_thread.id) is None
    assert len(store.list_messages(old_thread.id)) == 0
    assert store.get_thread(recent_thread.id) is not None
    assert len(store.list_messages(recent_thread.id)) == 1


@pytest.mark.unit
def test_archive_old_proposals(temp_db: Path, archive_dir: Path) -> None:
    """Terminal action proposals older than 180 days are gzipped and removed."""
    store = ConversationStore(temp_db)
    thread = store.create_thread(title="Proposal thread", kind="direct", participants=["barb"])
    msg = store.add_message(thread_id=thread.id, author_kind="role", author="barb", kind="action_proposal", body_md="")

    old_ts = "2026-01-01T12:00:00Z"
    # Old executed proposal
    old_p = store.create_proposal(
        message_id=msg.id,
        thread_id=thread.id,
        action="staff.dispatch",
        params={"role": "researcher"},
        risk="low",
    )
    with store._lock:  # noqa: SLF001
        store._conn.execute(  # noqa: SLF001
            "UPDATE action_proposals SET state = 'done', created_at = ?, decided_by = 'operator:alice', "
            "decided_at = ? WHERE id = ?",
            (old_ts, old_ts, old_p.id),
        )

    # Recent proposed proposal (should not be archived)
    recent_p = store.create_proposal(
        message_id=msg.id,
        thread_id=thread.id,
        action="staff.review_pr",
        params={"pr": 123},
        risk="medium",
    )

    archived_count, archive_files = archive_old_proposals(
        store=store,
        retention_days=180,
        archive_dir=archive_dir,
        reference_date="2026-09-26T12:00:00Z",
    )

    assert archived_count == 1
    assert len(archive_files) == 1
    with gzip.open(archive_files[0], "rt", encoding="utf-8") as gz:
        data = [json.loads(line) for line in gz if line.strip()]
    assert len(data) == 1
    assert data[0]["id"] == old_p.id
    assert data[0]["state"] == "done"

    # Verify DB
    assert store.get_proposal(old_p.id) is None
    assert store.get_proposal(recent_p.id) is not None


@pytest.mark.unit
def test_archive_old_runs_and_events(temp_db: Path, archive_dir: Path) -> None:
    """Terminal runs and their events older than 180 days are gzipped and removed."""
    store = RunStore(temp_db)
    old_ts = "2026-01-01T12:00:00Z"

    # Old completed run
    old_run = RunRecord(
        id="run-old-123",
        role="researcher",
        provider="claude",
        model="claude-3-5-sonnet",
        machine="nodeA",
        repo="repo-a",
        target_kind="prompt",
        target_ref="main",
        prompt="Old research prompt",
        status="succeeded",
        created_at=old_ts,
    )
    store.create_run(old_run)
    store.append_event(old_run.id, "queued", "queued on nodeA")
    store.append_event(old_run.id, "finish", "finished")

    # Recent active run
    recent_run = RunRecord(
        id="run-recent-456",
        role="developer",
        provider="codex",
        model="gpt-4o",
        machine="nodeA",
        repo="repo-a",
        target_kind="prompt",
        target_ref="main",
        prompt="Recent active prompt",
        status="running",
        created_at="2026-09-25T12:00:00Z",
    )
    store.create_run(recent_run)
    store.append_event(recent_run.id, "queued", "queued on nodeA")

    archived_count, archive_files = archive_old_runs(
        store=store,
        retention_days=180,
        archive_dir=archive_dir,
        reference_date="2026-09-26T12:00:00Z",
    )

    assert archived_count == 1
    assert len(archive_files) == 1
    with gzip.open(archive_files[0], "rt", encoding="utf-8") as gz:
        data = [json.loads(line) for line in gz if line.strip()]
    assert len(data) == 1
    assert data[0]["run"]["id"] == old_run.id
    assert len(data[0]["events"]) == 2

    # Verify DB
    assert store.get_run(old_run.id) is None
    assert len(store.events_after(old_run.id)) == 0
    assert store.get_run(recent_run.id) is not None
    assert len(store.events_after(recent_run.id)) == 1


@pytest.mark.unit
def test_archive_old_work_items_and_events(temp_db: Path, archive_dir: Path) -> None:
    """Terminal work items older than 180 days are gzipped and removed."""
    store = WorkItemStore(temp_db)
    old_ts = "2026-01-01T12:00:00Z"

    # Old terminal work item
    old_item = store.create_work_item(
        title="Fix legacy bug",
        requested_by="operator:alice",
        owner_role="developer",
        thread_id="thread-old",
    )
    store.transition_state(old_item.id, "in_progress", actor="operator:alice")
    store.transition_state(old_item.id, "done", actor="operator:alice", reason="Completed")
    with store._lock:  # noqa: SLF001
        conn = store._get_conn()  # noqa: SLF001
        try:
            conn.execute(
                "UPDATE work_items SET created_at = ?, updated_at = ? WHERE id = ?",
                (old_ts, old_ts, old_item.id),
            )
            conn.commit()
        finally:
            conn.close()

    # Recent work item
    recent_item = store.create_work_item(
        title="Active feature task",
        requested_by="operator:bob",
        owner_role="developer",
        thread_id="thread-recent",
    )

    archived_count, archive_files = archive_old_work_items(
        store=store,
        retention_days=180,
        archive_dir=archive_dir,
        reference_date="2026-09-26T12:00:00Z",
    )

    assert archived_count == 1
    assert len(archive_files) == 1
    with gzip.open(archive_files[0], "rt", encoding="utf-8") as gz:
        data = [json.loads(line) for line in gz if line.strip()]
    assert len(data) == 1
    assert data[0]["id"] == old_item.id
    assert data[0]["state"] == "done"

    # Verify DB
    assert store.get_work_item(old_item.id) is None
    assert store.get_work_item(recent_item.id) is not None


@pytest.mark.unit
def test_run_retention_archival_orchestrator(temp_db: Path, archive_dir: Path) -> None:
    """run_retention_archival runs across conversations, proposals, runs, and work items."""
    conv_store = ConversationStore(temp_db)
    run_store = RunStore(temp_db)
    wi_store = WorkItemStore(temp_db)

    results = run_retention_archival(
        conv_store=conv_store,
        run_store=run_store,
        work_item_store=wi_store,
        retention_days=180,
        archive_dir=archive_dir,
        reference_date="2026-09-26T12:00:00Z",
    )

    assert "conversations" in results
    assert "proposals" in results
    assert "runs" in results
    assert "work_items" in results
    assert isinstance(results["conversations"]["archived_count"], int)


@pytest.mark.unit
def test_export_thread_markdown_and_json() -> None:
    """Thread export produces clean Markdown and JSON representations."""
    thread = ThreadRecord(
        id="th-100",
        title="Architecture Discussion",
        kind="direct",
        participants=["barb", "operator:alice"],
        created_by="operator:alice",
        status="open",
        created_at="2026-09-26T10:00:00Z",
        updated_at="2026-09-26T10:15:00Z",
    )
    messages = [
        MessageRecord(
            id="msg-1",
            thread_id=thread.id,
            seq=1,
            author_kind="user",
            author="operator:alice",
            kind="text",
            body_md="Let us discuss the ADR.",
            created_at="2026-09-26T10:00:00Z",
        ),
        MessageRecord(
            id="msg-2",
            thread_id=thread.id,
            seq=2,
            author_kind="role",
            author="barb",
            kind="text",
            body_md="Understood. Routing to architect.",
            created_at="2026-09-26T10:01:00Z",
        ),
    ]

    # Markdown export
    md_text = export_thread_markdown(thread, messages)
    assert "# Thread: Architecture Discussion" in md_text
    assert "th-100" in md_text
    assert "operator:alice" in md_text
    assert "Let us discuss the ADR." in md_text
    assert "Routing to architect." in md_text

    # JSON export
    json_text = export_thread_json(thread, messages)
    parsed = json.loads(json_text)
    assert parsed["thread"]["id"] == "th-100"
    assert parsed["thread"]["title"] == "Architecture Discussion"
    assert len(parsed["messages"]) == 2
    assert parsed["messages"][0]["seq"] == 1
    assert parsed["messages"][1]["author"] == "barb"
