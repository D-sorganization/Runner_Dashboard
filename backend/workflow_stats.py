"""Workflow duration statistics — persistent store + GitHub polling + aggregation.

This module is imported by server.py and provides:

- ``init_db(path)``           — create/open the SQLite file and ensure schema.
- ``collect_once()``          — single poll of recent runs across the org,
                                 inserting any not-yet-recorded completed runs.
- ``collect_loop(interval)``  — infinite background loop (called from FastAPI
                                 lifespan).
- ``get_summary(...)``        — return P50/P95 duration stats per workflow or
                                 per repo over a rolling window.
- ``get_timeseries(...)``     — return a time-bucketed duration series for
                                 trend charting.

SQLite is embedded so there are no extra infrastructure moving parts: the
database lives next to the server at ``~/actions-runners/dashboard/stats.db``
by default (configurable via ``STATS_DB_PATH`` env var). Writes are batched
in a single transaction per collection pass.

The collector only records *completed* runs so that queued/in-progress
state transitions don't pollute the duration series. Once a run is
recorded, it is never updated (runs are immutable once conclusion is set).
"""

from __future__ import annotations

import asyncio
import datetime as _dt_mod
import json
import logging
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime
timedelta = _dt_mod.timedelta

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id            INTEGER PRIMARY KEY,
    repo              TEXT    NOT NULL,
    workflow_name     TEXT    NOT NULL,
    workflow_id       INTEGER NOT NULL,
    head_branch       TEXT,
    event             TEXT,
    status            TEXT,
    conclusion        TEXT,
    created_at        TEXT    NOT NULL,
    run_started_at    TEXT,
    updated_at        TEXT,
    queued_seconds    REAL,
    duration_seconds  REAL,
    runner_label      TEXT,
    inserted_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wfr_workflow ON workflow_runs(repo, workflow_name, created_at);
CREATE INDEX IF NOT EXISTS idx_wfr_created  ON workflow_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_wfr_conclusion ON workflow_runs(conclusion);

CREATE TABLE IF NOT EXISTS collection_state (
    key       TEXT PRIMARY KEY,
    value     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _db_path() -> Path:
    """Resolve the database file location (env-overridable)."""
    return Path(
        os.environ.get(
            "STATS_DB_PATH",
            str(Path.home() / "actions-runners" / "dashboard" / "stats.db"),
        )
    )


def init_db(path: Path | None = None) -> Path:
    """Create the DB file + schema if missing; return resolved path."""
    path = path or _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
    log.info("workflow_stats: DB initialized at %s", path)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    # GitHub returns ISO 8601 with trailing Z
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _compute_durations(run: dict[str, Any]) -> tuple[float | None, float | None]:
    """Return (queued_seconds, duration_seconds) from a GitHub runs payload item."""
    created = _parse_ts(run.get("created_at"))
    started = _parse_ts(run.get("run_started_at"))
    updated = _parse_ts(run.get("updated_at"))
    queued = (started - created).total_seconds() if (created and started) else None
    # run_started_at is when the job actually began; updated_at is the last
    # touch (typically completion for a conclusion-bearing run).
    duration = (
        (updated - started).total_seconds()
        if (started and updated and run.get("conclusion"))
        else None
    )
    return (queued, duration)


async def _gh_api_json(path: str, timeout: int = 20) -> Any:
    """Invoke `gh api <path>` and return parsed JSON (or None on failure)."""
    proc = await asyncio.create_subprocess_exec(
        "gh",
        "api",
        path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        log.warning("workflow_stats: gh api %s timed out", path)
        return None
    if proc.returncode != 0:
        log.debug(
            "workflow_stats: gh api %s failed rc=%s: %s",
            path,
            proc.returncode,
            err[:200],
        )
        return None
    try:
        return json.loads(out.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("workflow_stats: JSON decode error on %s: %s", path, exc)
        return None


async def _list_repos(org: str) -> list[str]:
    """List repo names for the org. Falls back to an empty list on failure."""
    data = await _gh_api_json(
        f"/orgs/{org}/repos?per_page=100&sort=updated&direction=desc", timeout=30
    )
    if not isinstance(data, list):
        return []
    return [r["name"] for r in data if isinstance(r, dict) and not r.get("archived")]


async def collect_once(
    org: str, per_repo_pages: int = 1, per_page: int = 30
) -> dict[str, int]:
    """Pull recent workflow runs from GitHub and persist new completed ones.

    Returns a dict with ``{scanned_repos, new_runs}`` for logging.
    """
    repos = await _list_repos(org)
    new_rows: list[tuple[Any, ...]] = []
    now_iso = datetime.now(UTC).isoformat()

    # Fetch in small concurrent batches to respect rate limits
    semaphore = asyncio.Semaphore(5)

    async def fetch(repo: str) -> list[dict]:
        async with semaphore:
            data = await _gh_api_json(
                f"/repos/{org}/{repo}/actions/runs?per_page={per_page}"
            )
        if not isinstance(data, dict):
            return []
        return [r for r in data.get("workflow_runs", []) if r.get("conclusion")]

    results = await asyncio.gather(*[fetch(r) for r in repos], return_exceptions=True)

    existing_ids: set[int] = set()
    with _connect() as conn:
        for row in conn.execute(
            "SELECT run_id FROM workflow_runs WHERE created_at >= ?",
            ((datetime.now(UTC) - timedelta(days=14)).isoformat(),),
        ):
            existing_ids.add(int(row["run_id"]))

    for repo, runs_or_exc in zip(repos, results, strict=True):
        if isinstance(runs_or_exc, BaseException):
            log.debug("workflow_stats: repo %s errored: %s", repo, runs_or_exc)
            continue
        for run in runs_or_exc:
            rid = int(run["id"])
            if rid in existing_ids:
                continue
            queued, duration = _compute_durations(run)
            # Best-effort runner label: the runs endpoint doesn't include it
            # directly, but we have "labels" on the referenced jobs. Defer that
            # to a separate enrichment step to avoid N+1 API cost here.
            new_rows.append(
                (
                    rid,
                    repo,
                    run.get("name") or run.get("display_title") or "?",
                    int(run.get("workflow_id") or 0),
                    run.get("head_branch"),
                    run.get("event"),
                    run.get("status"),
                    run.get("conclusion"),
                    run.get("created_at"),
                    run.get("run_started_at"),
                    run.get("updated_at"),
                    queued,
                    duration,
                    None,  # runner_label — left null for now
                    now_iso,
                )
            )

    if new_rows:
        with _connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO workflow_runs
                (run_id, repo, workflow_name, workflow_id, head_branch, event,
                 status, conclusion, created_at, run_started_at, updated_at,
                 queued_seconds, duration_seconds, runner_label, inserted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                new_rows,
            )
            conn.execute(
                "INSERT OR REPLACE INTO collection_state(key, value, updated_at) VALUES (?, ?, ?)",
                ("last_collection", now_iso, now_iso),
            )

    summary = {"scanned_repos": len(repos), "new_runs": len(new_rows)}
    log.info("workflow_stats: collect_once %s", summary)
    return summary


async def collect_loop(org: str, interval_seconds: float = 600.0) -> None:
    """Background loop: poll every ``interval_seconds`` forever.

    Called from the FastAPI lifespan handler. Logs and continues on errors
    so a transient GitHub hiccup doesn't take the collector down.
    """
    init_db()
    # Small initial delay so the server can start serving traffic first.
    await asyncio.sleep(30)
    while True:
        try:
            await collect_once(org)
        except Exception as exc:  # noqa: BLE001
            log.exception("workflow_stats: collector tick failed: %s", exc)
        await asyncio.sleep(interval_seconds)


def _percentile(values: list[float], p: float) -> float | None:
    """Simple inclusive-linear-interpolation percentile for small windows."""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def get_summary(days: int = 14, group_by: str = "workflow") -> dict[str, Any]:
    """Aggregate durations per workflow (or per repo) over a rolling window.

    Returns rows with ``count, success_rate, p50_duration, p95_duration,
    p50_queued, p95_queued`` for ``group_by in {"workflow","repo"}``.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    if group_by not in ("workflow", "repo"):
        group_by = "workflow"

    key_cols = {"workflow": "repo, workflow_name", "repo": "repo"}[group_by]
    with _connect() as conn:
        sql = f"SELECT {key_cols}, conclusion, duration_seconds, queued_seconds FROM workflow_runs WHERE created_at >= ? AND duration_seconds IS NOT NULL"  # nosec B608 — key_cols from allow-list dict  # noqa: E501
        rows = conn.execute(  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query  # noqa: E501
            sql, (cutoff,)
        ).fetchall()

    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for r in rows:
        if group_by == "workflow":
            key: tuple[str, ...] = (r["repo"], r["workflow_name"])
        else:
            key = (r["repo"],)
        g = groups.setdefault(
            key, {"dur": [], "q": [], "success": 0, "fail": 0, "total": 0}
        )
        g["total"] += 1
        if r["conclusion"] == "success":
            g["success"] += 1
        elif r["conclusion"] == "failure":
            g["fail"] += 1
        if r["duration_seconds"] is not None:
            g["dur"].append(float(r["duration_seconds"]))
        if r["queued_seconds"] is not None:
            g["q"].append(float(r["queued_seconds"]))

    out = []
    for key, g in groups.items():
        row: dict[str, Any] = {
            "repo": key[0],
            "count": g["total"],
            "success": g["success"],
            "failure": g["fail"],
            "success_rate": (
                round(g["success"] / g["total"] * 100, 1) if g["total"] else 0
            ),
            "p50_duration": _percentile(g["dur"], 0.50),
            "p95_duration": _percentile(g["dur"], 0.95),
            "p50_queued": _percentile(g["q"], 0.50),
            "p95_queued": _percentile(g["q"], 0.95),
        }
        if group_by == "workflow":
            row["workflow_name"] = key[1]
        out.append(row)

    out.sort(key=lambda x: (x["count"], x["p95_duration"] or 0), reverse=True)
    return {"window_days": days, "group_by": group_by, "rows": out}


def get_timeseries(
    days: int = 30,
    bucket_hours: int = 24,
    repo: str | None = None,
    workflow_name: str | None = None,
) -> dict[str, Any]:
    """Return bucketed median duration + count per time bucket."""
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    where = ["created_at >= ?", "duration_seconds IS NOT NULL"]
    params: list[Any] = [cutoff]
    if repo:
        where.append("repo = ?")
        params.append(repo)
    if workflow_name:
        where.append("workflow_name = ?")
        params.append(workflow_name)

    with _connect() as conn:
        sql = f"SELECT created_at, duration_seconds, queued_seconds FROM workflow_runs WHERE {' AND '.join(where)} ORDER BY created_at"  # nosec B608 — where clauses from internal allow-list  # noqa: E501
        rows = conn.execute(  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query  # noqa: E501
            sql, params
        ).fetchall()

    buckets: dict[str, list[float]] = {}
    q_buckets: dict[str, list[float]] = {}
    bucket_seconds = bucket_hours * 3600
    for r in rows:
        ts = _parse_ts(r["created_at"])
        if ts is None:
            continue
        epoch = int(ts.timestamp() // bucket_seconds) * bucket_seconds
        key = datetime.fromtimestamp(epoch, tz=UTC).isoformat()
        buckets.setdefault(key, []).append(float(r["duration_seconds"]))
        if r["queued_seconds"] is not None:
            q_buckets.setdefault(key, []).append(float(r["queued_seconds"]))

    series = []
    for key in sorted(buckets.keys()):
        durs = buckets[key]
        qs = q_buckets.get(key, [])
        series.append(
            {
                "t": key,
                "count": len(durs),
                "p50_duration": _percentile(durs, 0.50),
                "p95_duration": _percentile(durs, 0.95),
                "p50_queued": _percentile(qs, 0.50) if qs else None,
                "p95_queued": _percentile(qs, 0.95) if qs else None,
            }
        )
    return {
        "window_days": days,
        "bucket_hours": bucket_hours,
        "repo": repo,
        "workflow_name": workflow_name,
        "series": series,
    }


def get_recent_runs(limit: int = 50, repo: str | None = None) -> dict[str, Any]:
    """Most recently completed runs (for debug / spot-check)."""
    where = ["duration_seconds IS NOT NULL"]
    params: list[Any] = []
    if repo:
        where.append("repo = ?")
        params.append(repo)
    params.append(limit)

    with _connect() as conn:
        sql = f"SELECT run_id, repo, workflow_name, conclusion, head_branch, created_at, run_started_at, updated_at, queued_seconds, duration_seconds FROM workflow_runs WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?"  # nosec B608 — where clauses from internal allow-list  # noqa: E501
        rows = conn.execute(  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query  # noqa: E501
            sql, params
        ).fetchall()

    return {"rows": [dict(r) for r in rows]}
