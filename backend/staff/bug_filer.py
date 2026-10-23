"""Deduplicated, rate-limited failure to bug issue filer (#2236).

Consumes terminal staff run failures and critical FleetEvents, computing a normalized
fingerprint to deduplicate open issues and throttle repeats and retryable errors.
Default mode is 'dry_run' (configured via RD_BUG_FILER=off|dry_run|on).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.staff.bug_filer")

RETRYABLE_FAILURE_CLASSES = frozenset(
    {"auth_expired", "rate_limited", "upstream_unavailable", "provider_capacity", "providers_exhausted"}
)

_SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"gho_[A-Za-z0-9_]{20,}"),
    re.compile(r"svc_[A-Za-z0-9_.-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_]{20,}"),
    re.compile(r"AIza[0-9A-Za-z-_]{35}"),
    re.compile(r"bearer\s+[A-Za-z0-9_.-]{20,}", re.IGNORECASE),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bug_filer_state (
    fingerprint TEXT PRIMARY KEY,
    issue_number INTEGER,
    repo TEXT NOT NULL,
    failure_class TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_commented_at TEXT,
    last_filed_at TEXT,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS bug_filer_filed_idx ON bug_filer_state(last_filed_at);
"""


def redact_text(text: str) -> str:
    """Redact token and secret patterns from text."""
    if not text:
        return ""
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def normalize_error_message(text: str) -> str:
    """Normalize raw error text for stable fingerprinting."""
    if not text:
        return ""
    # Normalize path separators
    t = text.replace("\\", "/")
    # Strip ISO timestamps
    t = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?", "<timestamp>", t)
    # Strip UUIDs
    t = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", t, flags=re.IGNORECASE)
    # Strip hex memory addresses
    t = re.sub(r"0x[0-9a-fA-F]+", "<hex>", t)
    # Strip run IDs
    t = re.sub(r"run-[0-9a-zA-Z_-]+", "<run_id>", t)
    # Strip file paths
    t = re.sub(r"(?:[A-Za-z]:)?/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", "<path>", t)
    # Strip numbers
    t = re.sub(r"\b\d+\b", "<num>", t)
    # Collapse whitespace
    return re.sub(r"\s+", " ", t).strip()


def compute_failure_fingerprint(failure_class: str, role: str, repo: str, error: str) -> str:
    """Compute 16-char deterministic fingerprint from normalized failure metadata."""
    norm = normalize_error_message(error)
    raw = f"{failure_class}|{role}|{repo}|{norm[:200]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class BugFiler:
    def __init__(
        self,
        db_path: Path | None = None,
        gh_client: Any = None,
        retryable_threshold: int = 3,
        max_issues_per_day: int = 5,
    ) -> None:
        if db_path is None:
            config_dir = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR")
            base = Path(config_dir).expanduser() if config_dir else Path("~/.config/runner-dashboard").expanduser()
            db_path = base / "bug_filer.sqlite3"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.gh_client = gh_client
        self.retryable_threshold = retryable_threshold
        self.max_issues_per_day = max_issues_per_day

        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)

    @property
    def mode(self) -> str:
        return os.environ.get("RD_BUG_FILER", "dry_run").strip().lower()

    def process_failure(
        self,
        failure_class: str,
        role: str,
        repo: str,
        error: str,
        last_line: str = "",
        run_id: str = "",
    ) -> dict[str, Any]:
        """Process terminal run failure with dedup, rate limiting, and dry_run awareness."""
        if self.mode == "off":
            return {"action": "ignored", "reason": "filer_off"}

        fingerprint = compute_failure_fingerprint(failure_class, role, repo, error)
        now_dt = datetime.now(UTC)
        now_iso = now_dt.isoformat()

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM bug_filer_state WHERE fingerprint=?", (fingerprint,)).fetchone()

                if row:
                    count = row["occurrence_count"] + 1
                    conn.execute(
                        "UPDATE bug_filer_state SET occurrence_count=?, last_seen_at=? WHERE fingerprint=?",
                        (count, now_iso, fingerprint),
                    )
                    issue_num = row["issue_number"]
                    last_commented = row["last_commented_at"]
                else:
                    count = 1
                    issue_num = None
                    last_commented = None
                    conn.execute(
                        "INSERT INTO bug_filer_state "
                        "(fingerprint, issue_number, repo, failure_class, "
                        "occurrence_count, first_seen_at, last_seen_at) "
                        "VALUES (?, NULL, ?, ?, 1, ?, ?)",
                        (fingerprint, repo, failure_class, now_iso, now_iso),
                    )

                # Transient/retryable check
                if failure_class in RETRYABLE_FAILURE_CLASSES and count < self.retryable_threshold:
                    return {"action": "pending_threshold", "count": count, "fingerprint": fingerprint}

                # If issue already exists -> deduplicate or comment if > 24h
                if issue_num is not None:
                    should_comment = False
                    if not last_commented:
                        should_comment = True
                    else:
                        try:
                            comment_dt = datetime.fromisoformat(str(last_commented).replace("Z", "+00:00"))
                            if (now_dt - comment_dt).total_seconds() >= 86400.0:
                                should_comment = True
                        except Exception:
                            should_comment = True

                    if not should_comment:
                        return {"action": "deduped", "issue_number": issue_num, "fingerprint": fingerprint}

                    # Add repeat comment
                    if self.mode == "dry_run":
                        return {"action": "would_comment", "dry_run": True, "fingerprint": fingerprint}

                    if self.gh_client:
                        comment_body = (
                            f"<!-- rd-bug-fingerprint: {fingerprint} -->\n"
                            f"Repeat occurrence #{count} detected for run `{run_id}`.\n\n"
                            f"**Last line**: `{redact_text(last_line)[:200]}`"
                        )
                        self.gh_client.create_comment(repo=repo, issue_number=issue_num, body=comment_body)
                        conn.execute(
                            "UPDATE bug_filer_state SET last_commented_at=? WHERE fingerprint=?",
                            (now_iso, fingerprint),
                        )
                    return {"action": "commented", "issue_number": issue_num, "fingerprint": fingerprint}

                # No issue filed yet: check daily cap
                day_ago = (now_dt - timedelta(days=1)).isoformat()
                filed_row = conn.execute(
                    "SELECT COUNT(*) FROM bug_filer_state WHERE last_filed_at IS NOT NULL AND last_filed_at >= ?",
                    (day_ago,),
                ).fetchone()
                filed_today = filed_row[0] if filed_row else 0
                if filed_today >= self.max_issues_per_day:
                    return {"action": "daily_cap_digest", "fingerprint": fingerprint}

                if self.mode == "dry_run":
                    return {"action": "would_file", "dry_run": True, "fingerprint": fingerprint}

                # File issue
                if self.gh_client:
                    title = f"[bug] {failure_class} in {role}: {redact_text(error)[:80]}"
                    body = (
                        f"<!-- rd-bug-fingerprint: {fingerprint} -->\n"
                        f"## Automated Staff Failure Report\n"
                        f"- **Failure Class**: `{failure_class}`\n"
                        f"- **Role**: `{role}`\n"
                        f"- **Repo**: `{repo}`\n"
                        f"- **Run ID**: `{run_id}`\n"
                        f"- **Logs**: `GET /api/v1/diagnostics/logs?run_id={run_id}`\n\n"
                        f"### Last Line\n```\n{redact_text(last_line)}\n```\n"
                    )
                    created = self.gh_client.create_issue(
                        repo=repo,
                        title=title,
                        body=body,
                        labels=["bug"],
                    )
                    new_issue_num = created.get("number") if isinstance(created, dict) else None
                    conn.execute(
                        "UPDATE bug_filer_state "
                        "SET issue_number=?, last_filed_at=?, last_commented_at=? WHERE fingerprint=?",
                        (new_issue_num, now_iso, now_iso, fingerprint),
                    )
                    return {"action": "filed", "issue_number": new_issue_num, "fingerprint": fingerprint}

                return {"action": "skipped_no_client", "fingerprint": fingerprint}

        except Exception as exc:
            log.warning("Bug filer encountered error: %s", exc)
            return {"action": "error", "error": str(exc), "fingerprint": fingerprint}


_BUG_FILER: BugFiler | None = None


def get_bug_filer() -> BugFiler:
    global _BUG_FILER
    if _BUG_FILER is None:
        _BUG_FILER = BugFiler()
    return _BUG_FILER


def set_bug_filer(filer: BugFiler | None) -> None:
    global _BUG_FILER
    _BUG_FILER = filer


def handle_terminal_failure(run: Any) -> dict[str, Any]:
    """Consume a terminal staff run failure and invoke BugFiler."""
    if not run:
        return {"action": "ignored", "reason": "empty_run"}
    status = getattr(run, "status", "")
    if status not in ("failed", "timeout"):
        return {"action": "ignored", "reason": f"non_terminal_status_{status}"}
    return get_bug_filer().process_failure(
        failure_class=getattr(run, "failure_class", "") or "unknown",
        role=getattr(run, "role", "") or "staff",
        repo=getattr(run, "repo", "") or "Runner_Dashboard",
        error=getattr(run, "error", "") or "",
        last_line=getattr(run, "last_line", "") or "",
        run_id=getattr(run, "id", "") or "",
    )


def handle_fleet_event(event: Any) -> dict[str, Any]:
    """Consume a FleetEvent with severity warning/critical/error."""
    if not event:
        return {"action": "ignored", "reason": "empty_event"}
    severity = getattr(event, "severity", "")
    if severity not in ("warning", "critical", "error"):
        return {"action": "ignored", "reason": f"severity_{severity}"}
    return get_bug_filer().process_failure(
        failure_class=getattr(event, "kind", "fleet_event"),
        role=f"node-{getattr(event, 'node', '') or 'hub'}",
        repo="Runner_Dashboard",
        error=f"{getattr(event, 'title', '')}: {getattr(event, 'detail', '')}",
        last_line=getattr(event, "detail", "") or "",
        run_id="",
    )
