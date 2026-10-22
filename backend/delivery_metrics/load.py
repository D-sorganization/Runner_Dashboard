"""Read stored delivery-metrics facts from SQLite (#2120)."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from delivery_metrics.models import CheckRunSnapshot, PrSnapshot
from time_utils import parse_github_ts


def _require_ts(value: str) -> datetime:
    parsed = parse_github_ts(value)
    if parsed is None:
        raise ValueError(f"invalid timestamp: {value!r}")
    return parsed


def _row_pr(row: sqlite3.Row) -> PrSnapshot:
    return PrSnapshot(
        repo_full=row["repo_full"],
        number=int(row["number"]),
        title=row["title"],
        state=row["state"],
        merged=bool(row["merged"]),
        draft=bool(row["draft"]),
        head_sha=row["head_sha"],
        base_ref=row["base_ref"],
        author_login=row["author_login"],
        created_at=_require_ts(row["created_at"]),
        updated_at=_require_ts(row["updated_at"]),
        closed_at=parse_github_ts(row["closed_at"]),
        merged_at=parse_github_ts(row["merged_at"]),
        additions=int(row["additions"]),
        deletions=int(row["deletions"]),
        changed_files=int(row["changed_files"]),
        html_url=row["html_url"],
    )


def _row_check(row: sqlite3.Row) -> CheckRunSnapshot:
    return CheckRunSnapshot(
        repo_full=row["repo_full"],
        head_sha=row["head_sha"],
        check_run_id=int(row["check_run_id"]),
        name=row["name"],
        status=row["status"],
        conclusion=row["conclusion"],
        started_at=parse_github_ts(row["started_at"]),
        completed_at=parse_github_ts(row["completed_at"]),
        details_url=row["details_url"],
        app_slug=row["app_slug"],
    )


def list_pulls_between(
    conn: sqlite3.Connection,
    *,
    since: datetime,
    until: datetime,
    repo_full: str | None = None,
) -> tuple[PrSnapshot, ...]:
    """Pulls whose ``updated_at`` falls in ``[since, until]``."""
    since_s = since.isoformat().replace("+00:00", "Z")
    until_s = until.isoformat().replace("+00:00", "Z")
    if repo_full:
        rows = conn.execute(
            """
            SELECT * FROM dm_pulls
            WHERE repo_full=? AND updated_at >= ? AND updated_at <= ?
            ORDER BY updated_at DESC
            """,
            (repo_full, since_s, until_s),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM dm_pulls
            WHERE updated_at >= ? AND updated_at <= ?
            ORDER BY updated_at DESC
            """,
            (since_s, until_s),
        ).fetchall()
    return tuple(_row_pr(r) for r in rows)


def get_pull(conn: sqlite3.Connection, repo_full: str, number: int) -> PrSnapshot | None:
    row = conn.execute(
        "SELECT * FROM dm_pulls WHERE repo_full=? AND number=?",
        (repo_full, number),
    ).fetchone()
    return _row_pr(row) if row else None


def check_runs_for_heads(
    conn: sqlite3.Connection,
    repo_full: str,
    head_shas: set[str],
) -> dict[str, tuple[CheckRunSnapshot, ...]]:
    """Map head SHA → check runs (empty when ``head_shas`` is empty)."""
    if not head_shas:
        return {}
    placeholders = ",".join("?" for _ in head_shas)
    rows = conn.execute(
        f"""
        SELECT * FROM dm_check_runs
        WHERE repo_full=? AND head_sha IN ({placeholders})
        """,
        (repo_full, *sorted(head_shas)),
    ).fetchall()
    out: dict[str, list[CheckRunSnapshot]] = {}
    for row in rows:
        out.setdefault(row["head_sha"], []).append(_row_check(row))
    return {sha: tuple(items) for sha, items in out.items()}
