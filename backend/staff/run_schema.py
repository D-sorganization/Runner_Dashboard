"""Additive column migrations for the staff ``runs`` table (split from ``staff.store``, 500-line cap)."""

from __future__ import annotations

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    machine TEXT NOT NULL,
    repo TEXT NOT NULL DEFAULT '',
    target_kind TEXT NOT NULL,
    target_ref TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    exit_code INTEGER,
    cost_usd REAL NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    workdir TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    transcript_path TEXT NOT NULL DEFAULT '',
    lease_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    last_line TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS runs_status_idx ON runs(status);
CREATE INDEX IF NOT EXISTS runs_created_idx ON runs(created_at);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run_idx ON events(run_id, seq);
CREATE TABLE IF NOT EXISTS dispatcher_fallbacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL,
    principal TEXT NOT NULL,
    reason TEXT NOT NULL,
    task_class TEXT NOT NULL,
    est_grok_turns INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(principal, idempotency_key)
);
CREATE INDEX IF NOT EXISTS dispatcher_fallbacks_created_idx ON dispatcher_fallbacks(created_at);
"""

# Columns added after the first schema shipped. Applied with a guarded
# ``ALTER TABLE ... ADD COLUMN`` so an existing store upgrades in place and a
# rollback to the previous code keeps working (extra columns are ignored).
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("cost_method", "TEXT NOT NULL DEFAULT ''"),
    ("strategy_mode", "TEXT NOT NULL DEFAULT ''"),
    ("outcome", "TEXT NOT NULL DEFAULT ''"),
    ("failure_class", "TEXT NOT NULL DEFAULT ''"),
    ("pid", "INTEGER"),
    ("retryable", "INTEGER NOT NULL DEFAULT 0"),
    ("remediation", "TEXT NOT NULL DEFAULT ''"),
    ("on_behalf_of", "TEXT NOT NULL DEFAULT ''"),
    ("retry_of", "TEXT NOT NULL DEFAULT ''"),
    ("attempt", "INTEGER NOT NULL DEFAULT 1"),
    ("max_attempts", "INTEGER NOT NULL DEFAULT 2"),
    ("next_attempt_at", "TEXT"),
    ("fallback_provider", "TEXT NOT NULL DEFAULT ''"),
    ("thread_id", "TEXT NOT NULL DEFAULT ''"),
    ("work_item_id", "TEXT NOT NULL DEFAULT ''"),
    ("verification", "TEXT NOT NULL DEFAULT ''"),
    ("verification_detail", "TEXT NOT NULL DEFAULT ''"),
    ("pr_number", "INTEGER"),
    ("artifact", "TEXT NOT NULL DEFAULT ''"),
    ("origin_node", "TEXT NOT NULL DEFAULT ''"),
    # Per-run monotonic run-card version counter for Barb authority dedupe (#1800, BR-06).
    ("card_version", "INTEGER NOT NULL DEFAULT 0"),
    ("provider_chain", "TEXT NOT NULL DEFAULT ''"),
    ("model_tier", "TEXT NOT NULL DEFAULT ''"),
    ("skill_pin", "TEXT NOT NULL DEFAULT ''"),
    ("standards_version", "TEXT NOT NULL DEFAULT ''"),
    ("standards_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("context_stamp", "TEXT NOT NULL DEFAULT '{}'"),
    ("library_work_ids", "TEXT NOT NULL DEFAULT '[]'"),
    ("injected_lesson_ids", "TEXT NOT NULL DEFAULT '[]'"),
    ("skipped_lesson_ids", "TEXT NOT NULL DEFAULT '{}'"),
    ("lessons_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("execution_mode", "TEXT NOT NULL DEFAULT 'local'"),
    ("remote_session_url", "TEXT NOT NULL DEFAULT ''"),
    ("dispatched_by", "TEXT NOT NULL DEFAULT ''"),
    ("dispatch_origin", "TEXT NOT NULL DEFAULT 'other'"),
)
