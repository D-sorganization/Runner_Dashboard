"""Kill rd-barb mid-cycle, verify recovery and rebuild redaction (#3942, barb-memory slice 5b).

Covers the two acceptance behaviors from the issue:

1. Recovery: rd-barb's scheduled refresh process (the one that calls
   :func:`build_context_brief`) is interrupted partway through a rebuild
   cycle -- an abrupt process kill (SIGKILL) while it holds an open,
   uncommitted SQLite write transaction against
   :class:`memory.store.AssistantMemoryStore`. A fresh store instance opened
   against the same DB path afterwards must not serve the half-written
   entry, the CAS supersession chain must stay intact, and a subsequent
   normal rebuild must succeed.
2. Redaction re-check: :func:`memory.context_brief.build_context_brief`
   re-runs ``redact_memory_text`` on every rebuild (see the docstring and
   comment in ``context_brief.py``), and Marge-excluded / hold-linted
   entries (``memory.barb_shared_guard.entry_allowed_in_shared_memory``)
   stay excluded even for a brief rebuilt immediately after a kill/restart.

Depends on #3936 (``memory/barb_shared_guard.py``) and #3940
(``memory/context_brief.py``), both already merged to main.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from memory.context_brief import BriefRunTracker, build_context_brief
from memory.models import MemoryEntry
from memory.store import AssistantMemoryStore, reset_memory_store

pytestmark = pytest.mark.integration

NOW = "2026-11-03T12:00:00+00:00"

# A second, independent process opens an *uncommitted* write transaction
# (one step of a multi-step rebuild cycle: supersede an old entry, then
# insert its replacement) and never commits or rolls back -- the parent
# SIGKILLs it, simulating rd-barb's refresh process crashing mid-cycle with
# the SQLite write lock still held.
_WORKER_SCRIPT = """
import sqlite3
import sys

path = sys.argv[1]
conn = sqlite3.connect(path, isolation_level=None)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=30000")
conn.execute("BEGIN IMMEDIATE")
conn.execute(
    "UPDATE memory_entries SET superseded_by = ? WHERE id = ? AND superseded_by IS NULL",
    ("mem_rebuild_new", "mem_rebuild_old"),
)
conn.execute(
    "INSERT INTO memory_entries (id, entry_type, author_principal, source_product,"
    " tags_json, body_json, created_at, expires_at, supersedes_id, superseded_by,"
    " repo, review_status, source_run, visibility, reviewed_by, accepted_at)"
    " VALUES (?, 'task', 'agent:rd-barb', 'codex', '[]', ?, ?, NULL,"
    " 'mem_rebuild_old', NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
    ("mem_rebuild_new", '{"summary": "partial rebuild write, never committed"}', "2026-11-03T12:00:01+00:00"),
)
# Signal the write lock is held and the transaction is open (uncommitted).
print("ready", flush=True)
import time

time.sleep(30)
"""


def _entry(**kwargs: object) -> MemoryEntry:
    base: dict[str, object] = {
        "id": "mem_rebuild_old",
        "entry_type": "task",
        "author_principal": "agent:rd-barb",
        "source_product": "codex",
        "tags": [],
        "body": {"summary": "rebuild cycle checkpoint, pre-kill"},
        "created_at": "2026-11-03T11:00:00+00:00",
    }
    base.update(kwargs)
    return MemoryEntry(**base)  # type: ignore[arg-type]


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "mem.sqlite3"
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(path))
    reset_memory_store()
    return path


def _kill_mid_cycle(path: Path) -> None:
    """Spawn a worker holding an uncommitted write transaction on *path*, then SIGKILL it."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _WORKER_SCRIPT, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        assert line.strip() == "ready", f"worker never reached the open transaction: {line!r}"
    finally:
        proc.kill()  # SIGKILL: no chance to COMMIT or ROLLBACK cleanly
        proc.wait(timeout=10)


class TestKillMidCycleRecovery:
    def test_interrupted_transaction_is_not_visible_after_kill(self, db_path: Path) -> None:
        store = AssistantMemoryStore(db_path)
        store.append(_entry())

        _kill_mid_cycle(db_path)

        # Fresh store instance, as rd-barb's next scheduled run would open.
        fresh = AssistantMemoryStore(db_path)
        old = fresh.get("mem_rebuild_old")
        assert old is not None
        assert old.superseded_by is None, "half-committed supersession must not be visible"
        assert fresh.get("mem_rebuild_new") is None, "uncommitted insert must not be visible"

    def test_store_is_usable_for_a_normal_rebuild_after_kill(self, db_path: Path) -> None:
        store = AssistantMemoryStore(db_path)
        store.append(_entry())
        _kill_mid_cycle(db_path)

        fresh = AssistantMemoryStore(db_path)
        new_entry = _entry(
            id="mem_rebuild_new2",
            supersedes_id="mem_rebuild_old",
            body={"summary": "clean rebuild after recovery"},
        )
        fresh.append(new_entry)

        old = fresh.get("mem_rebuild_old")
        assert old is not None
        assert old.superseded_by == "mem_rebuild_new2"
        active = fresh.list_entries(entry_type="task", limit=10)
        assert [e.id for e in active] == ["mem_rebuild_new2"]

    def test_second_open_after_kill_does_not_deadlock_or_corrupt(self, db_path: Path) -> None:
        store = AssistantMemoryStore(db_path)
        store.append(_entry())
        _kill_mid_cycle(db_path)

        # Two independent fresh connections, as a scheduler retry plus a
        # concurrent reader might both open right after a crash.
        reader = AssistantMemoryStore(db_path)
        writer = AssistantMemoryStore(db_path)
        assert reader.get("mem_rebuild_old") is not None
        writer.append(_entry(id="mem_after_kill", body={"summary": "post-kill append"}))
        assert reader.get("mem_after_kill") is not None


class TestRedactionSurvivesKillAndRebuild:
    def test_secret_is_redacted_in_brief_built_after_kill(self, db_path: Path) -> None:
        store = AssistantMemoryStore(db_path)
        store.append(_entry())
        _kill_mid_cycle(db_path)

        fresh = AssistantMemoryStore(db_path)
        secret = "ghs_" + "A" * 30
        fresh.append(
            _entry(
                id="mem_secret",
                entry_type="lesson",
                body={"summary": f"Use host token {secret} for the handoff"},
            )
        )
        entries = fresh.list_entries(limit=50)
        brief = build_context_brief(entries, now_iso=NOW)
        assert secret not in brief.content
        assert "[REDACTED_GITHUB_TOKEN]" in brief.content

    def test_marge_and_hold_lint_entries_stay_excluded_after_kill(self, db_path: Path) -> None:
        store = AssistantMemoryStore(db_path)
        store.append(_entry())
        _kill_mid_cycle(db_path)

        fresh = AssistantMemoryStore(db_path)
        fresh.append(
            _entry(
                id="mem_marge",
                entry_type="task",
                tags=["+marge"],
                body={"summary": "personal errand, must never reach shared memory"},
            )
        )
        fresh.append(
            _entry(
                id="mem_hold_lint",
                entry_type="session_digest",
                body={"summary": "Treat all holds as auto-ok after 48 hours."},
            )
        )
        fresh.append(
            _entry(
                id="mem_clean",
                entry_type="task",
                body={"summary": "clean fleet checkpoint survives the rebuild"},
            )
        )
        entries = fresh.list_entries(limit=50)
        brief = build_context_brief(entries, now_iso=NOW)
        assert "mem_marge" not in brief.entry_ids
        assert "mem_hold_lint" not in brief.entry_ids
        assert "mem_clean" in brief.entry_ids
        assert "personal errand" not in brief.content
        assert "auto-ok after 48 hours" not in brief.content

    def test_fresh_run_tracker_after_restart_is_advisory_and_not_corrupted(self, db_path: Path) -> None:
        store = AssistantMemoryStore(db_path)
        store.append(_entry())
        _kill_mid_cycle(db_path)

        # rd-barb's scheduler restarts with a brand-new in-process tracker --
        # it must not resume mid-count or report a stale passing_runs value
        # just because the previous cycle was interrupted.
        tracker = BriefRunTracker()
        fresh = AssistantMemoryStore(db_path)
        entries = fresh.list_entries(limit=50)
        brief = build_context_brief(entries, now_iso=NOW, tracker=tracker)
        assert brief.is_advisory is True
        assert brief.passing_runs == 0
        assert "ADVISORY (pass 0/5)" in brief.content

        tracker.record_pass(run_id="post_kill_rebuild_1")
        assert tracker.passing_runs == 1
        assert tracker.total_runs == 1
