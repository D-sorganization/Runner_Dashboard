"""Unit tests for conversation store, migrations, redaction, and proposals (SC-B2, Issue #1305)."""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.staff.audit import StaffAuditRecord, StaffAuditStore, reset_audit_store
from backend.staff.conversations import (
    ConversationStore,
    ConversationsUnavailableError,
    reset_conversation_store,
)
from backend.staff.redaction import redact_sensitive_content
from backend.staff.store import RunRecord, RunStore


@pytest.fixture
def clean_db(tmp_path: Path) -> Iterator[Path]:
    """Provide a clean SQLite database path and reset singletons."""
    db_file = tmp_path / "staff_runs.sqlite3"
    reset_audit_store()
    reset_conversation_store()
    yield db_file
    reset_audit_store()
    reset_conversation_store()


# ── REDACTION TESTS ─────────────────────────────────────────────────────────


def test_redact_github_tokens() -> None:
    tok_ghp = "ghp_" + ("1" * 36)
    tok_gho = "gho_" + ("A" * 36)
    tok_pat = "github_pat_" + ("2" * 30)
    text = f"Here is my token: {tok_ghp} and an oauth {tok_gho} and fine-grained {tok_pat}"
    redacted = redact_sensitive_content(text)
    assert "ghp_" not in redacted
    assert "gho_" not in redacted
    assert "github_pat_" not in redacted
    assert "[REDACTED_GITHUB_TOKEN]" in redacted


def test_redact_aws_keys() -> None:
    aws_key = "AK" + "IA" + ("Z" * 16)
    sec_key = "w" * 40
    part1 = f"Access key {aws_key} and "
    part2 = f"secret aws_secret_access_key = '{sec_key}'"
    text = part1 + part2
    redacted = redact_sensitive_content(text)
    assert aws_key not in redacted
    assert "[REDACTED_AWS_KEY]" in redacted
    assert sec_key not in redacted


def test_redact_api_keys_and_bearer() -> None:
    jwt_tok = "ey" + "JhbGciOi" + "JIUzI1NiIsInR5cCI" + "." + "e30" + "." + "t-ID"
    api_k = "sk-ant-" + "api03-" + ("a" * 35)
    text = f"Bearer {jwt_tok} and {api_k}"
    redacted = redact_sensitive_content(text)
    assert jwt_tok not in redacted
    assert "Bearer [REDACTED_TOKEN]" in redacted
    assert "sk-ant-" not in redacted
    assert "[REDACTED_API_KEY]" in redacted


def test_redact_private_keys() -> None:
    prefix = "-" * 5
    key_type = "RSA " + "PRIVATE " + "KEY"
    pem = (
        f"{prefix}BEGIN {key_type}{prefix}\n"
        f"MIIEowIBAAKCAQEA0{'b' * 30}\n"
        f"{prefix}END {key_type}{prefix}"
    )  # pragma: allowlist secret
    redacted = redact_sensitive_content(f"Here is key:\n{pem}\nDone.")
    assert "MIIEow" not in redacted
    assert "[REDACTED_PRIVATE_KEY]" in redacted


def test_redact_private_lan_ips_only() -> None:
    text = (
        "Node IPs: 10.0.1.25, 172.16.5.4, 192.168.1.100, 100.64.0.1. "
        "Public IPs: 8.8.8.8, 1.1.1.1, loopback: 127.0.0.1, version: 10.1.2"
    )
    redacted = redact_sensitive_content(text)
    assert "10.0.1.25" not in redacted
    assert "172.16.5.4" not in redacted
    assert "192.168.1.100" not in redacted
    assert "100.64.0.1" not in redacted
    assert "[REDACTED_IP]" in redacted
    # Non-private should be retained
    assert "8.8.8.8" in redacted
    assert "1.1.1.1" in redacted
    assert "127.0.0.1" in redacted
    assert "10.1.2" in redacted


# ── MIGRATIONS & BACKUP TESTS ───────────────────────────────────────────────


def test_migration_on_existing_db_preserves_data_and_creates_backup(
    clean_db: Path,
) -> None:
    """Pre-existing runs and audit entries survive conversation migrations with a backup created."""
    # Seed pre-existing tables
    run_store = RunStore(clean_db)
    run_store.create_run(
        RunRecord(
            id="run-pre-existing-1",
            role="architect",
            provider="claude",
            model="claude-3-5-sonnet",
            machine="DeskComputer",
            repo="D-sorganization/Runner_Dashboard",
            target_kind="prompt",
            target_ref="",
            prompt="Initial test prompt",
        )
    )
    audit_store = StaffAuditStore(clean_db)
    audit_store.record(
        StaffAuditRecord(
            principal="operator:admin",
            action="dispatch",
            surface="ui",
            target="run-pre-existing-1",
        )
    )
    assert clean_db.stat().st_size > 0

    # Apply conversation store migrations
    conv_store = ConversationStore(clean_db)
    assert conv_store.status.available is True

    # Verify backup file was created
    backups = list(clean_db.parent.glob(f"{clean_db.name}.bak.*"))
    assert len(backups) >= 1

    # Verify previous data remains untouched
    assert run_store.get_run("run-pre-existing-1") is not None
    assert len(audit_store.list_entries(limit=10)) == 1

    # Verify migration table
    with conv_store._lock:  # noqa: SLF001
        applied = conv_store._conn.execute(  # noqa: SLF001
            "SELECT version, name FROM schema_migrations ORDER BY version ASC"
        ).fetchall()
        assert len(applied) >= 1
        assert applied[0]["version"] == 1

    # Re-instantiating store is idempotent and does not make redundant backups
    initial_backup_count = len(backups)
    conv_store2 = ConversationStore(clean_db)
    assert conv_store2.status.available is True
    backups2 = list(clean_db.parent.glob(f"{clean_db.name}.bak.*"))
    assert len(backups2) == initial_backup_count


def test_migration_failure_preserves_old_db_and_sets_degraded_banner(
    tmp_path: Path,
) -> None:
    """When a migration fails, the old DB is untouched and degraded status is set."""
    db_file = tmp_path / "broken_staff_runs.sqlite3"
    run_store = RunStore(db_file)
    run_store.create_run(
        RunRecord(
            id="run-important-99",
            role="coder",
            provider="codex",
            model=None,
            machine="DeskComputer",
            repo="D-sorganization/Runner_Dashboard",
            target_kind="prompt",
            target_ref="",
            prompt="Important work",
        )
    )

    # Subclass with invalid migration SQL
    class BrokenConversationStore(ConversationStore):
        MIGRATIONS = [(1, "broken_migration", "INVALID SQL SYNTAX HERE;")]

    store = BrokenConversationStore(db_file)
    assert store.status.available is False
    assert "migration" in store.status.banner_message.lower()
    assert store.status.error != ""

    # Verify pre-existing data is untouched
    run_store_check = RunStore(db_file)
    assert run_store_check.get_run("run-important-99") is not None

    # Operations fail visibly with ConversationsUnavailableError
    with pytest.raises(ConversationsUnavailableError):
        store.create_thread(title="Test", kind="direct", participants=["user"])


# ── THREAD CRUD & UNREAD COUNTERS ──────────────────────────────────────────


def test_thread_crud_and_unread_tracking(clean_db: Path) -> None:
    store = ConversationStore(clean_db)
    thread = store.create_thread(
        title="CI Diagnostics",
        kind="direct",
        participants=["user:dieter", "role:architect"],
        created_by="user:dieter",
    )
    assert thread.id.startswith("th_") or len(thread.id) > 0
    assert thread.title == "CI Diagnostics"
    assert thread.status == "open"
    assert thread.unread_counters.get("user:dieter", 0) == 0
    assert thread.unread_counters.get("role:architect", 0) == 0

    # Fetch
    fetched = store.get_thread(thread.id)
    assert fetched is not None
    assert fetched.title == thread.title
    assert fetched.participants == ["user:dieter", "role:architect"]

    # List threads
    threads = store.list_threads(kind="direct")
    assert len(threads) == 1
    assert threads[0].id == thread.id

    # Filter by participant
    assert len(store.list_threads(participant="user:dieter")) == 1
    assert len(store.list_threads(participant="user:someone_else")) == 0

    # Update thread
    updated = store.update_thread(thread.id, title="CI Diagnostics & Fixes")
    assert updated is not None
    assert updated.title == "CI Diagnostics & Fixes"

    # Archive thread
    archived = store.archive_thread(thread.id, principal="user:dieter")
    assert archived is not None
    assert archived.status == "archived"
    assert len(store.list_threads(status="open")) == 0
    assert len(store.list_threads(status="archived")) == 1


# ── MESSAGES, SEQUENCING & IDEMPOTENCY ─────────────────────────────────────


def test_messages_seq_monotonic_and_redaction(clean_db: Path) -> None:
    store = ConversationStore(clean_db)
    thread = store.create_thread(
        title="Token leak discussion",
        kind="direct",
        participants=["user:alice", "role:architect"],
        created_by="user:alice",
    )

    leak_tok = "ghp_" + ("9" * 36)
    msg1 = store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="user:alice",
        kind="text",
        body_md=f"Investigate {leak_tok} on 192.168.1.50",
    )
    assert msg1.seq == 1
    assert "ghp_" not in msg1.body_md
    assert "[REDACTED_GITHUB_TOKEN]" in msg1.body_md
    assert "192.168.1.50" not in msg1.body_md
    assert "[REDACTED_IP]" in msg1.body_md

    msg2 = store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author="role:architect",
        kind="text",
        body_md="Acknowledged. Scanning repo now.",
    )
    assert msg2.seq == 2

    # Thread unread counter: user:alice sent msg1 (architect unread = 1), architect sent msg2 (alice unread = 1)
    th = store.get_thread(thread.id)
    assert th is not None
    assert th.last_message_at == msg2.created_at
    assert th.unread_counters.get("user:alice") == 1
    assert th.unread_counters.get("role:architect") == 1

    # Mark read for alice
    store.mark_thread_read(thread.id, "user:alice")
    th_read = store.get_thread(thread.id)
    assert th_read is not None
    assert th_read.unread_counters.get("user:alice") == 0
    assert th_read.unread_counters.get("role:architect") == 1

    # Messages list
    messages = store.list_messages(thread.id)
    assert len(messages) == 2
    assert [m.seq for m in messages] == [1, 2]


def test_message_idempotency_key(clean_db: Path) -> None:
    store = ConversationStore(clean_db)
    thread = store.create_thread(
        title="Idempotency Test",
        kind="direct",
        participants=["user:bob", "role:barb"],
        created_by="user:bob",
    )

    msg1 = store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="user:bob",
        kind="text",
        body_md="First send",
        idempotency_key="key-abc-123",
    )
    assert msg1.seq == 1

    # Duplicate send with same idempotency key
    msg2 = store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author="user:bob",
        kind="text",
        body_md="Duplicate send with different body",
        idempotency_key="key-abc-123",
    )
    assert msg2.id == msg1.id
    assert msg2.seq == 1
    assert msg2.body_md == "First send"

    # Only 1 message exists in thread
    messages = store.list_messages(thread.id)
    assert len(messages) == 1

    # Different thread with same idempotency key should succeed
    thread2 = store.create_thread(title="Second Thread", kind="direct", participants=["user:bob"])
    msg_other = store.add_message(
        thread_id=thread2.id,
        author_kind="user",
        author="user:bob",
        kind="text",
        body_md="Message in thread 2",
        idempotency_key="key-abc-123",
    )
    assert msg_other.id != msg1.id
    assert msg_other.thread_id == thread2.id


# ── ACTION PROPOSALS & AUDIT TRAIL ─────────────────────────────────────────


def test_action_proposal_state_machine_and_audit(clean_db: Path) -> None:
    store = ConversationStore(clean_db)
    audit_store = StaffAuditStore(clean_db)

    thread = store.create_thread(title="Action Approval", kind="direct", participants=["user:admin"])
    msg = store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author="role:architect",
        kind="action_proposal",
        body_md="I propose dispatching a fix run.",
    )

    # 1. Create proposal
    prop = store.create_proposal(
        message_id=msg.id,
        thread_id=thread.id,
        action="dispatch",
        params={"role": "coder", "prompt": "fix issue #1305"},
        risk="medium",
        principal="role:architect",
    )
    assert prop.id.startswith("prop_") or len(prop.id) > 0
    assert prop.state == "proposed"
    assert prop.risk == "medium"

    # Verify audit row created
    audits = audit_store.list_entries(action="proposal_create", thread_id=thread.id)
    assert len(audits) == 1
    assert audits[0].target == prop.id

    # 2. Approve proposal
    decided = store.decide_proposal(
        proposal_id=prop.id,
        state="approved",
        decided_by="operator:admin",
        reason="Approved plan",
    )
    assert decided.state == "approved"
    assert decided.decided_by == "operator:admin"
    assert decided.reason == "Approved plan"
    assert decided.decided_at is not None

    audits_approve = audit_store.list_entries(action="proposal_approve", thread_id=thread.id)
    assert len(audits_approve) == 1
    assert audits_approve[0].principal == "operator:admin"

    # 3. Transition to executing -> done
    executing = store.transition_proposal_state(prop.id, new_state="executing")
    assert executing.state == "executing"
    audits_exec = audit_store.list_entries(action="proposal_execute", thread_id=thread.id)
    assert len(audits_exec) == 1

    done = store.transition_proposal_state(prop.id, new_state="done")
    assert done.state == "done"

    # Invalid state transition should fail
    with pytest.raises(ValueError, match="Invalid proposal transition"):
        store.transition_proposal_state(prop.id, new_state="approved")


def test_action_proposal_denial(clean_db: Path) -> None:
    store = ConversationStore(clean_db)
    thread = store.create_thread(title="Action Denial", kind="direct", participants=["user:admin"])
    msg = store.add_message(thread_id=thread.id, author_kind="role", author="role:coder", body_md="Prop")
    prop = store.create_proposal(message_id=msg.id, thread_id=thread.id, action="dispatch")

    denied = store.decide_proposal(
        proposal_id=prop.id,
        state="denied",
        decided_by="operator:admin",
        reason="Not needed",
    )
    assert denied.state == "denied"
    assert denied.decided_by == "operator:admin"
    assert denied.reason == "Not needed"

    # Cannot transition denied proposal to executing
    with pytest.raises(ValueError, match="Invalid proposal transition"):
        store.transition_proposal_state(prop.id, new_state="executing")


# ── CONCURRENCY TESTS ───────────────────────────────────────────────────────


def test_concurrent_writers_under_wal(clean_db: Path) -> None:
    """Two concurrent threads writing messages to the store complete without locking errors."""
    store = ConversationStore(clean_db)
    thread = store.create_thread(title="Concurrent Thread", kind="group", participants=["t1", "t2"])

    def worker(worker_id: str, count: int) -> list[str]:
        # Dedicated store instance pointing to same WAL file
        local_store = ConversationStore(clean_db)
        created_ids = []
        for i in range(count):
            msg = local_store.add_message(
                thread_id=thread.id,
                author_kind="user",
                author=f"worker_{worker_id}",
                kind="text",
                body_md=f"Message {i} from worker {worker_id}",
            )
            created_ids.append(msg.id)
            time.sleep(0.001)
        return created_ids

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker, "A", 25)
        f2 = executor.submit(worker, "B", 25)
        ids_a = f1.result()
        ids_b = f2.result()

    assert len(ids_a) == 25
    assert len(ids_b) == 25

    # Check total messages and strict monotonic sequencing
    all_msgs = store.list_messages(thread.id, limit=100)
    assert len(all_msgs) == 50
    seqs = [m.seq for m in all_msgs]
    assert seqs == list(range(1, 51))


def test_list_non_terminal_reply_messages(clean_db: Path) -> None:
    """list_non_terminal_reply_messages returns pending and streaming reply messages, excluding user messages."""
    store = ConversationStore(clean_db)
    thread = store.create_thread(title="Test Non-terminal", kind="direct", participants=["user", "barb"])

    # 1. User message in complete state
    store.add_message(thread.id, author_kind="user", author="user", body_md="Hello", delivery="complete")
    # 2. User message in pending state (must be excluded)
    store.add_message(thread.id, author_kind="user", author="user", body_md="Pending user msg", delivery="pending")
    # 3. Role message in pending state (must be included)
    m_pending = store.add_message(thread.id, author_kind="role", author="barb", body_md="", delivery="pending")
    # 4. Role message in streaming state (must be included)
    m_streaming = store.add_message(
        thread.id, author_kind="role", author="barb", body_md="tokens...", delivery="streaming"
    )
    # 5. Role message in complete state (must be excluded)
    store.add_message(thread.id, author_kind="role", author="barb", body_md="Done", delivery="complete")
    # 6. Role message in failed state (must be excluded)
    store.add_message(
        thread.id,
        author_kind="role",
        author="barb",
        body_md="Failed",
        delivery="failed",
        meta={"failure_class": "rate_limited"},
    )

    non_terminal = store.list_non_terminal_reply_messages()
    assert [m.id for m in non_terminal] == [m_pending.id, m_streaming.id]

    # Verify failure_class property and to_dict
    m_failed = store.list_messages(thread.id)[-1]
    assert m_failed.failure_class == "rate_limited"
    assert m_failed.to_dict()["failure_class"] == "rate_limited"
