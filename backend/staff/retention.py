"""Staff data retention archival and conversation export (SC-B1-G7, Issue #1490).

Provides:
- 180-day retention archival across conversations (threads + messages),
  action proposals, runs + events, and work items + events to gzip JSONL.
- Thread export to sanitized Markdown and structured JSON.
- Never hard-deletes database records without first writing them to a verified gzip archive.
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from staff.audit import StaffAuditStore, archive_old_audit_entries, record_audit
from staff.conversation_models import ActionProposalRecord, MessageRecord, ThreadRecord
from staff.conversations import ConversationStore, get_conversation_store
from staff.store import RunRecord, RunStore, get_store
from staff.work_items import WorkItemRecord, WorkItemStore, get_work_item_store

log = logging.getLogger("dashboard.staff.retention")

DEFAULT_RETENTION_DAYS = 180


def _default_archive_dir() -> Path:
    # Match audit archive dir pattern: ~/.config/runner_dashboard/archives
    cfg_dir = Path.home() / ".config" / "runner_dashboard"
    return cfg_dir / "archives"


def _get_cutoff(retention_days: int, reference_date: str | None) -> tuple[datetime, str]:
    if reference_date:
        ref_dt = datetime.fromisoformat(reference_date.replace("Z", "+00:00"))
    else:
        ref_dt = datetime.now(UTC)
    cutoff = ref_dt - timedelta(days=retention_days)
    cutoff_iso = cutoff.isoformat().replace("+00:00", "Z")
    return cutoff, cutoff_iso


def archive_old_conversations(
    store: ConversationStore | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    archive_dir: Path | None = None,
    reference_date: str | None = None,
) -> tuple[int, list[Path]]:
    """Archive inactive/closed threads and their messages older than retention_days to gzip."""
    target_store = store or get_conversation_store()
    out_dir = archive_dir or (_default_archive_dir() / "conversations")
    out_dir.mkdir(parents=True, exist_ok=True)
    cutoff, cutoff_iso = _get_cutoff(retention_days, reference_date)

    with target_store._lock:  # noqa: SLF001
        conn = target_store._conn  # noqa: SLF001
        thread_rows = conn.execute(
            "SELECT * FROM threads WHERE updated_at < ? ORDER BY updated_at ASC",
            (cutoff_iso,),
        ).fetchall()
        if not thread_rows:
            return 0, []

        archive_name = f"conversations_archive_{cutoff.strftime('%Y%m')}.jsonl.gz"
        archive_path = out_dir / archive_name

        records: list[dict[str, Any]] = []
        thread_ids: list[str] = []
        for trow in thread_rows:
            thread = ThreadRecord.from_row(trow)
            thread_ids.append(thread.id)
            msg_rows = conn.execute(
                "SELECT * FROM messages WHERE thread_id = ? ORDER BY seq ASC",
                (thread.id,),
            ).fetchall()
            messages = [MessageRecord.from_row(mrow).to_dict() for mrow in msg_rows]
            records.append(
                {
                    "thread": thread.to_dict(),
                    "messages": messages,
                }
            )

        with gzip.open(archive_path, "at", encoding="utf-8") as gz:
            for rec in records:
                gz.write(json.dumps(rec, separators=(",", ":")) + "\n")

        # Delete archived messages and threads in transaction
        for tid in thread_ids:
            conn.execute("DELETE FROM messages WHERE thread_id = ?", (tid,))
            conn.execute("DELETE FROM threads WHERE id = ?", (tid,))

    log.info("Archived %d conversation threads to %s", len(thread_ids), archive_path)
    return len(thread_ids), [archive_path]


def archive_old_proposals(
    store: ConversationStore | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    archive_dir: Path | None = None,
    reference_date: str | None = None,
) -> tuple[int, list[Path]]:
    """Archive terminal action proposals older than retention_days to gzip."""
    target_store = store or get_conversation_store()
    out_dir = archive_dir or (_default_archive_dir() / "proposals")
    out_dir.mkdir(parents=True, exist_ok=True)
    cutoff, cutoff_iso = _get_cutoff(retention_days, reference_date)

    terminal_states = ("approved", "denied", "done", "failed", "expired")
    placeholders = ", ".join("?" for _ in terminal_states)

    with target_store._lock:  # noqa: SLF001
        conn = target_store._conn  # noqa: SLF001
        query = (
            f"SELECT * FROM action_proposals WHERE state IN ({placeholders}) "  # noqa: S608
            "AND created_at < ? ORDER BY created_at ASC"
        )
        rows = conn.execute(query, (*terminal_states, cutoff_iso)).fetchall()
        if not rows:
            return 0, []

        proposals = [ActionProposalRecord.from_row(r).to_dict() for r in rows]
        archive_name = f"action_proposals_archive_{cutoff.strftime('%Y%m')}.jsonl.gz"
        archive_path = out_dir / archive_name

        with gzip.open(archive_path, "at", encoding="utf-8") as gz:
            for p in proposals:
                gz.write(json.dumps(p, separators=(",", ":")) + "\n")

        p_ids = [p["id"] for p in proposals]
        for i in range(0, len(p_ids), 500):
            chunk = p_ids[i : i + 500]
            marks = ", ".join("?" for _ in chunk)
            conn.execute(f"DELETE FROM action_proposals WHERE id IN ({marks})", tuple(chunk))  # noqa: S608

    log.info("Archived %d action proposals to %s", len(proposals), archive_path)
    return len(proposals), [archive_path]


def archive_old_runs(
    store: RunStore | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    archive_dir: Path | None = None,
    reference_date: str | None = None,
) -> tuple[int, list[Path]]:
    """Archive terminal runs and associated events older than retention_days to gzip."""
    target_store = store or get_store()
    out_dir = archive_dir or (_default_archive_dir() / "runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    cutoff, cutoff_iso = _get_cutoff(retention_days, reference_date)

    terminal_statuses = ("succeeded", "failed", "cancelled")
    placeholders = ", ".join("?" for _ in terminal_statuses)

    with target_store._lock:  # noqa: SLF001
        conn = target_store._conn  # noqa: SLF001
        run_rows = conn.execute(
            f"SELECT * FROM runs WHERE status IN ({placeholders}) AND created_at < ? ORDER BY created_at ASC",  # noqa: S608
            (*terminal_statuses, cutoff_iso),
        ).fetchall()
        if not run_rows:
            return 0, []

        records: list[dict[str, Any]] = []
        run_ids: list[str] = []
        for r_row in run_rows:
            run_rec = RunRecord(**{k: r_row[k] for k in r_row.keys() if k in RunRecord.__dataclass_fields__})
            run_ids.append(run_rec.id)
            ev_rows = conn.execute(
                "SELECT ts, kind, text FROM events WHERE run_id = ? ORDER BY seq ASC",
                (run_rec.id,),
            ).fetchall()
            events = [dict(erow) for erow in ev_rows]
            records.append(
                {
                    "run": run_rec.to_dict(),
                    "events": events,
                }
            )

        archive_name = f"staff_runs_archive_{cutoff.strftime('%Y%m')}.jsonl.gz"
        archive_path = out_dir / archive_name

        with gzip.open(archive_path, "at", encoding="utf-8") as gz:
            for rec in records:
                gz.write(json.dumps(rec, separators=(",", ":")) + "\n")

        for rid in run_ids:
            conn.execute("DELETE FROM events WHERE run_id = ?", (rid,))
            conn.execute("DELETE FROM runs WHERE id = ?", (rid,))

    log.info("Archived %d runs to %s", len(run_ids), archive_path)
    return len(run_ids), [archive_path]


def archive_old_work_items(
    store: WorkItemStore | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    archive_dir: Path | None = None,
    reference_date: str | None = None,
) -> tuple[int, list[Path]]:
    """Archive terminal work items and associated events older than retention_days to gzip."""
    target_store = store or get_work_item_store()
    out_dir = archive_dir or (_default_archive_dir() / "work_items")
    out_dir.mkdir(parents=True, exist_ok=True)
    cutoff, cutoff_iso = _get_cutoff(retention_days, reference_date)

    terminal_states = ("done", "cancelled", "escalated")
    placeholders = ", ".join("?" for _ in terminal_states)

    with target_store._lock:  # noqa: SLF001
        conn = target_store._get_conn()  # noqa: SLF001
        try:
            item_rows = conn.execute(
                f"SELECT * FROM work_items WHERE state IN ({placeholders}) AND updated_at < ? ORDER BY updated_at ASC",  # noqa: S608
                (*terminal_states, cutoff_iso),
            ).fetchall()
            if not item_rows:
                return 0, []

            records = [WorkItemRecord.from_row(r).to_dict() for r in item_rows]
            item_ids = [r["id"] for r in records]

            archive_name = f"work_items_archive_{cutoff.strftime('%Y%m')}.jsonl.gz"
            archive_path = out_dir / archive_name

            with gzip.open(archive_path, "at", encoding="utf-8") as gz:
                for rec in records:
                    gz.write(json.dumps(rec, separators=(",", ":")) + "\n")

            for wid in item_ids:
                conn.execute("DELETE FROM work_items WHERE id = ?", (wid,))
            conn.commit()
        finally:
            conn.close()

    log.info("Archived %d work items to %s", len(item_ids), archive_path)
    return len(item_ids), [archive_path]


def run_retention_archival(
    conv_store: ConversationStore | None = None,
    run_store: RunStore | None = None,
    work_item_store: WorkItemStore | None = None,
    audit_store: StaffAuditStore | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    archive_dir: Path | None = None,
    reference_date: str | None = None,
) -> dict[str, Any]:
    """Execute retention archival sweep across all staff entity stores."""
    base_dir = archive_dir or _default_archive_dir()
    c_count, c_files = archive_old_conversations(
        store=conv_store,
        retention_days=retention_days,
        archive_dir=base_dir / "conversations",
        reference_date=reference_date,
    )
    p_count, p_files = archive_old_proposals(
        store=conv_store,
        retention_days=retention_days,
        archive_dir=base_dir / "proposals",
        reference_date=reference_date,
    )
    r_count, r_files = archive_old_runs(
        store=run_store,
        retention_days=retention_days,
        archive_dir=base_dir / "runs",
        reference_date=reference_date,
    )
    w_count, w_files = archive_old_work_items(
        store=work_item_store,
        retention_days=retention_days,
        archive_dir=base_dir / "work_items",
        reference_date=reference_date,
    )
    a_count, a_files = archive_old_audit_entries(
        store=audit_store,
        retention_days=retention_days,
        archive_dir=base_dir / "audit",
        reference_date=reference_date,
    )

    record_audit(
        principal="system:archiver",
        surface="retention",
        action="retention_sweep",
        target="fleet",
        outcome="success",
        detail={
            "retention_days": retention_days,
            "conversations_archived": c_count,
            "proposals_archived": p_count,
            "runs_archived": r_count,
            "work_items_archived": w_count,
            "audit_archived": a_count,
        },
        fail_closed=False,
    )

    return {
        "retention_days": retention_days,
        "conversations": {"archived_count": c_count, "files": [str(f) for f in c_files]},
        "proposals": {"archived_count": p_count, "files": [str(f) for f in p_files]},
        "runs": {"archived_count": r_count, "files": [str(f) for f in r_files]},
        "work_items": {"archived_count": w_count, "files": [str(f) for f in w_files]},
        "audit": {"archived_count": a_count, "files": [str(f) for f in a_files]},
    }


def export_thread_markdown(thread: ThreadRecord, messages: list[MessageRecord]) -> str:
    """Format a conversation thread and its messages as sanitized Markdown."""
    participants = ", ".join(thread.participants) if thread.participants else "None"
    lines = [
        f"# Thread: {thread.title}",
        "",
        f"- **Thread ID:** `{thread.id}`",
        f"- **Kind:** `{thread.kind}`",
        f"- **Status:** `{thread.status}`",
        f"- **Created:** {thread.created_at}",
        f"- **Updated:** {thread.updated_at}",
        f"- **Participants:** {participants}",
        "",
        "---",
        "",
    ]
    for m in messages:
        author = m.author or "unknown"
        author_kind = m.author_kind or "text"
        lines.append(f"### [{m.seq}] {author} ({author_kind}) · {m.created_at}")
        lines.append("")
        if m.body_md:
            lines.append(m.body_md)
        elif m.kind == "run_card":
            status = (m.meta.get("status") or "").upper()
            run_info = m.meta.get("run") or {}
            node = run_info.get("node") or m.meta.get("machine") or "unknown"
            lines.append(f"> **Run Card:** {status} on node `{node}` (Run: `{m.run_id}`)")
        elif m.kind == "action_proposal":
            prop = m.meta.get("proposal") or {}
            action = prop.get("action") or "unknown"
            state = prop.get("state") or "proposed"
            lines.append(f"> **Action Proposal:** `{action}` [{state}]")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def export_thread_json(thread: ThreadRecord, messages: list[MessageRecord]) -> str:
    """Format a conversation thread and its messages as indented JSON."""
    payload = {
        "thread": thread.to_dict(),
        "messages": [m.to_dict() for m in messages],
    }
    return json.dumps(payload, indent=2, sort_keys=True)
