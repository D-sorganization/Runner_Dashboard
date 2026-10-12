"""Additive column migrations for the staff ``runs`` table (split from ``staff.store``, 500-line cap)."""

from __future__ import annotations

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
)
