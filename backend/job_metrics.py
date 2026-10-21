"""Persistent storage and aggregation for Actions job timing metrics."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from workflow_analysis import infer_machine_from_runner_name
from workflow_stats import connect_stats_db, inclusive_percentile

from shared_scripts.ci_fleet_baseline.constants import NEEDS_CREATED_AT_NOTE
from shared_scripts.ci_fleet_baseline.window import parse_utc

GroupBy = Literal["repo", "event", "label", "host"]
GROUPINGS = frozenset({"repo", "event", "label", "host"})
JOB_COLUMNS = (
    "repo,job_id,run_id,run_attempt,event,workflow_name,job_name,status,conclusion,"
    "created_at,started_at,completed_at,queue_seconds,execute_seconds,runner_id,"
    "runner_name,runner_group_id,runner_group_name,labels_json,observed_at"
)


def _group_key(row: Any, group_by: GroupBy) -> str:
    if group_by == "repo":
        return str(row["repo"])
    if group_by == "event":
        return str(row["event"] or "Unknown")
    if group_by == "label":
        labels = json.loads(row["labels_json"])
        return ",".join(labels) if labels else "Unlabeled"
    return infer_machine_from_runner_name(row["runner_name"]) or "Unknown"


def _group_values(row: Any, group_by: GroupBy) -> list[str]:
    if group_by == "label":
        labels = json.loads(row["labels_json"])
        return list(dict.fromkeys(labels)) or ["Unlabeled"]
    return [_group_key(row, group_by)]


def _collection_coverage(conn: Any) -> dict[str, Any]:
    rows = conn.execute("SELECT value FROM collection_state WHERE key LIKE 'job_metrics_status:%'").fetchall()
    states = [json.loads(row["value"]) for row in rows]
    reasons = {reason for state in states for reason in state.get("partial_reasons", [])}
    incomplete_runs = conn.execute(
        "SELECT collection_reasons_json FROM workflow_job_runs WHERE collection_complete=0"
    ).fetchall()
    for row in incomplete_runs:
        try:
            reasons.update(json.loads(row["collection_reasons_json"]))
        except (TypeError, json.JSONDecodeError):
            reasons.add("stored_run_reasons_malformed")
    return {
        "complete": bool(states) and all(state.get("complete", False) for state in states) and not incomplete_runs,
        "organizations_reported": len(states),
        "partial_reasons": sorted(reasons),
        "caps": sorted({cap for state in states for cap in state.get("caps", [])}),
        "incomplete_runs": len(incomplete_runs),
    }


def save_job_batch(
    org: str,
    repo: str,
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    *,
    complete: bool,
    observed_at: str,
    run_updated_at: str | None,
    collection_reasons: list[str] | None = None,
) -> None:
    """Atomically upsert one attempt's jobs and collection progress."""
    from job_metric_semantics import job_durations

    run_id = int(run["id"])
    parent_attempt = positive_attempt(run.get("run_attempt"))
    with connect_stats_db() as conn:
        prior = conn.execute(
            "SELECT latest_attempt,run_updated_at,collection_reasons_json FROM workflow_job_runs "
            "WHERE org=? AND repo=? AND run_id=?",
            (org, repo, run_id),
        ).fetchone()
        reasons = set(collection_reasons or [])
        if prior and prior["latest_attempt"] == parent_attempt and prior["run_updated_at"] == run_updated_at:
            try:
                reasons.update(json.loads(prior["collection_reasons_json"]))
            except (TypeError, json.JSONDecodeError):
                reasons.add("stored_run_reasons_malformed")
        complete = complete and not reasons
        for job in jobs:
            queue_seconds, execute_seconds = job_durations(
                job.get("created_at"), job.get("started_at"), job.get("completed_at")
            )
            normalized = {
                "repo": repo,
                "job_id": int(job["id"]),
                "run_id": run_id,
                "run_attempt": positive_attempt(job.get("run_attempt")),
                "event": run.get("event"),
                "workflow_name": run.get("name"),
                "job_name": str(job.get("name") or "Unknown"),
                "status": job.get("status"),
                "conclusion": job.get("conclusion"),
                "created_at": _utc_timestamp(job.get("created_at")),
                "started_at": _utc_timestamp(job.get("started_at")),
                "completed_at": _utc_timestamp(job.get("completed_at")),
                "queue_seconds": queue_seconds,
                "execute_seconds": execute_seconds,
                "runner_id": job.get("runner_id"),
                "runner_name": job.get("runner_name"),
                "runner_group_id": job.get("runner_group_id"),
                "runner_group_name": job.get("runner_group_name"),
                "labels_json": json.dumps(sorted(set(job.get("labels") or []))),
                "observed_at": observed_at,
            }
            placeholders = ",".join("?" for _ in JOB_COLUMNS.split(","))
            updates = ",".join(f"{column}=excluded.{column}" for column in JOB_COLUMNS.split(",")[2:])
            conn.execute(
                f"INSERT INTO workflow_jobs ({JOB_COLUMNS}) VALUES ({placeholders}) "
                f"ON CONFLICT(repo,job_id) DO UPDATE SET {updates}",
                tuple(normalized[column] for column in JOB_COLUMNS.split(",")),
            )
        conn.execute(
            "INSERT INTO workflow_job_runs "
            "(org,repo,run_id,latest_attempt,run_updated_at,run_status,run_conclusion,"
            "collection_reasons_json,collection_complete,observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(org,repo,run_id) DO UPDATE SET "
            "latest_attempt=excluded.latest_attempt,run_updated_at=excluded.run_updated_at,"
            "run_status=excluded.run_status,run_conclusion=excluded.run_conclusion,"
            "collection_reasons_json=excluded.collection_reasons_json,"
            "collection_complete=excluded.collection_complete,observed_at=excluded.observed_at",
            (
                org,
                repo,
                run_id,
                parent_attempt,
                run_updated_at,
                run.get("status"),
                run.get("conclusion"),
                json.dumps(sorted(reasons)),
                int(complete),
                observed_at,
            ),
        )
        conn.commit()


def save_collection_state(org: str, value: dict[str, Any], *, key_suffix: str = "status") -> None:
    """Persist resumable cursor or latest coverage for an organization."""
    now = datetime.now(UTC).isoformat()
    with connect_stats_db() as conn:
        conn.execute(
            "INSERT INTO collection_state(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (f"job_metrics_{key_suffix}:{org}", json.dumps(value, sort_keys=True), now),
        )
        conn.commit()


def load_collection_state(org: str, *, key_suffix: str = "cursor") -> dict[str, Any]:
    """Load a persisted cursor, returning an empty object on first collection."""
    with connect_stats_db() as conn:
        row = conn.execute(
            "SELECT value FROM collection_state WHERE key=?", (f"job_metrics_{key_suffix}:{org}",)
        ).fetchone()
    return json.loads(row["value"]) if row else {}


def incomplete_run_count(org: str) -> int:
    """Count outstanding run records without confusing traversal with coverage."""
    with connect_stats_db() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM workflow_job_runs WHERE org=? AND collection_complete=0",
                (org,),
            ).fetchone()[0]
        )


def positive_attempt(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        attempt = value
    elif isinstance(value, str) and value.strip().isdigit():
        attempt = int(value.strip())
    else:
        return None
    return attempt if attempt > 0 else None


def completed_run_is_unchanged(org: str, repo: str, run: dict[str, Any]) -> bool:
    """Skip only fully collected completed runs with the same attempt/update marker."""
    updated_at = run.get("updated_at")
    attempt = positive_attempt(run.get("run_attempt"))
    if run.get("status") != "completed" or not run.get("conclusion") or not updated_at or not attempt:
        return False
    with connect_stats_db() as conn:
        row = conn.execute(
            "SELECT latest_attempt,run_updated_at,collection_complete FROM workflow_job_runs "
            "WHERE org=? AND repo=? AND run_id=?",
            (org, repo, int(run["id"])),
        ).fetchone()
    return bool(
        row and row["collection_complete"] and row["latest_attempt"] == attempt and row["run_updated_at"] == updated_at
    )


def _utc_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parse_utc(value).isoformat()
    except (AttributeError, TypeError, ValueError):
        return None


def get_job_metrics(
    days: int = 14,
    group_by: GroupBy = "repo",
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize stored job timings over a bounded rolling UTC window."""
    if not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")
    if group_by not in GROUPINGS:
        raise ValueError("group_by must be repo, event, label, or host")
    end = now or datetime.now(UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    else:
        end = end.astimezone(UTC)
    start = end - timedelta(days=days)
    with connect_stats_db() as conn:
        rows = conn.execute(
            "SELECT repo,event,runner_name,labels_json,created_at,queue_seconds,execute_seconds "
            "FROM workflow_jobs WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        coverage = _collection_coverage(conn)

    groups: dict[str, dict[str, Any]] = {}
    queue_samples = execute_samples = 0
    for row in rows:
        queue = float(row["queue_seconds"]) if row["queue_seconds"] is not None else None
        execute = float(row["execute_seconds"]) if row["execute_seconds"] is not None else None
        queue_samples += queue is not None
        execute_samples += execute is not None
        for key in _group_values(row, group_by):
            group = groups.setdefault(key, {"jobs": 0, "queue": [], "execute": []})
            group["jobs"] += 1
            if queue is not None:
                group["queue"].append(queue)
            if execute is not None:
                group["execute"].append(execute)

    result_rows = []
    for key, group in sorted(groups.items()):
        queue_values = group["queue"]
        execute_values = group["execute"]
        result_rows.append(
            {
                "key": key,
                "job_count": group["jobs"],
                "queue_count": len(queue_values),
                "execute_count": len(execute_values),
                "queue_p50_seconds": inclusive_percentile(queue_values, 0.50),
                "queue_p95_seconds": inclusive_percentile(queue_values, 0.95),
                "execute_p50_seconds": inclusive_percentile(execute_values, 0.50),
                "execute_p95_seconds": inclusive_percentile(execute_values, 0.95),
            }
        )
    return {
        "generated_at": end.isoformat(),
        "window": {"days": days, "start": start.isoformat(), "end": end.isoformat()},
        "group_by": group_by,
        "sample": {"jobs": len(rows), "queue": queue_samples, "execution": execute_samples},
        "coverage": coverage,
        "labels_are_non_additive": group_by == "label",
        "queue_semantics": NEEDS_CREATED_AT_NOTE,
        "rows": result_rows,
    }
