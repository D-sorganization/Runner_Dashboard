"""Workflow and machine performance analysis helpers.

The dashboard receives workflow runs from GitHub and enriches some of them with
job placement data.  This module keeps the aggregation logic pure so routes and
UI code can reuse the same definitions without duplicating success-rate and
duration math.
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter
from typing import Any

UTC = getattr(_dt, "UTC", _dt.timezone.utc)  # noqa: UP017


def infer_machine_from_runner_name(runner_name: str | None) -> str | None:
    """Map a GitHub runner name to the dashboard's canonical machine/pool name."""
    if not runner_name:
        return None
    value = runner_name.strip()
    token = "".join(ch for ch in value.lower() if ch.isalnum())
    if not token:
        return None
    if "github" in token or "hosted" in token or token.startswith("ubuntu"):
        return "GitHub Hosted"
    if "controltowernvme" in token or "nvme" in token:
        return "ControlTower-NVMe"
    if "controltowerssd" in token or token.endswith("ssd") or "ssd" in token:
        return "ControlTower-SSD"
    if "controltower" in token:
        return "ControlTower"
    if "deskcomputer" in token or token.startswith("desk") or "desktop" in token:
        return "DeskComputer"
    if "oglaptop" in token or "oglap" in token:
        return "OGLaptop"
    if "brick" in token:
        return "Brick"
    return value


def _parse_ts(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def run_duration_seconds(run: dict[str, Any]) -> float | None:
    """Return execution duration for a completed run, if timestamps are usable."""
    started = _parse_ts(run.get("run_started_at"))
    updated = _parse_ts(run.get("updated_at"))
    if not started or not updated:
        return None
    seconds = (updated - started).total_seconds()
    return seconds if seconds >= 0 else None


def _run_machine(run: dict[str, Any]) -> str:
    machine = run.get("machine_name")
    if machine:
        return str(machine)
    runner = run.get("runner_name")
    inferred = infer_machine_from_runner_name(str(runner) if runner else None)
    if inferred:
        return inferred
    for job in run.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        inferred = infer_machine_from_runner_name(job.get("runner_name"))
        if inferred:
            return inferred
    return "Unknown"


def _run_workflow(run: dict[str, Any]) -> str:
    return str(run.get("name") or run.get("workflow_name") or run.get("display_title") or "Unknown workflow")


def _run_repo(run: dict[str, Any]) -> str:
    repo = run.get("repo")
    if repo:
        return str(repo)
    repository = run.get("repository")
    if isinstance(repository, dict) and repository.get("name"):
        return str(repository["name"])
    return "Unknown repo"


def _empty_bucket() -> dict[str, Any]:
    return {"total": 0, "success": 0, "failure": 0, "cancelled": 0, "duration_seconds": []}


def _add_run(bucket: dict[str, Any], run: dict[str, Any]) -> None:
    bucket["total"] += 1
    conclusion = str(run.get("conclusion") or "").lower()
    if conclusion == "success":
        bucket["success"] += 1
    elif conclusion == "failure":
        bucket["failure"] += 1
    elif conclusion == "cancelled":
        bucket["cancelled"] += 1
    duration = run_duration_seconds(run)
    if duration is not None:
        bucket["duration_seconds"].append(duration)


def _finish_bucket(key: str, bucket: dict[str, Any], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    durations = bucket["duration_seconds"]
    total = bucket["total"]
    row = {
        "key": key,
        "count": total,
        "success": bucket["success"],
        "failure": bucket["failure"],
        "cancelled": bucket["cancelled"],
        "success_rate": round((bucket["success"] / total) * 100, 1) if total else 0,
        "avg_duration_seconds": round(sum(durations) / len(durations), 1) if durations else None,
    }
    if extra:
        row.update(extra)
    return row


def summarize_runs_by_workflow_and_machine(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize recent runs by workflow, machine, and workflow-machine pair."""
    by_workflow: dict[tuple[str, str], dict[str, Any]] = {}
    by_machine: dict[str, dict[str, Any]] = {}
    matrix: dict[tuple[str, str, str], dict[str, Any]] = {}
    failure_reasons: Counter[str] = Counter()

    for run in runs:
        repo = _run_repo(run)
        workflow = _run_workflow(run)
        machine = _run_machine(run)
        workflow_key = (repo, workflow)
        matrix_key = (repo, workflow, machine)
        _add_run(by_workflow.setdefault(workflow_key, _empty_bucket()), run)
        _add_run(by_machine.setdefault(machine, _empty_bucket()), run)
        _add_run(matrix.setdefault(matrix_key, _empty_bucket()), run)
        conclusion = str(run.get("conclusion") or "unknown").lower()
        if conclusion and conclusion != "success":
            failure_reasons[conclusion] += 1

    workflows = [
        _finish_bucket(f"{repo}/{workflow}", bucket, extra={"repo": repo, "workflow_name": workflow})
        for (repo, workflow), bucket in by_workflow.items()
    ]
    machines = [
        _finish_bucket(machine, bucket, extra={"machine_name": machine}) for machine, bucket in by_machine.items()
    ]
    matrix_rows = [
        _finish_bucket(
            f"{repo}/{workflow}@{machine}",
            bucket,
            extra={"repo": repo, "workflow_name": workflow, "machine_name": machine},
        )
        for (repo, workflow, machine), bucket in matrix.items()
    ]

    workflows.sort(key=lambda row: (row["failure"], row["avg_duration_seconds"] or 0, row["count"]), reverse=True)
    machines.sort(key=lambda row: (row["failure"], row["avg_duration_seconds"] or 0, row["count"]), reverse=True)
    matrix_rows.sort(key=lambda row: (row["failure"], row["avg_duration_seconds"] or 0, row["count"]), reverse=True)

    total = len(runs)
    success = sum(1 for run in runs if str(run.get("conclusion") or "").lower() == "success")
    return {
        "generated_at": _dt.datetime.now(UTC).isoformat(),
        "sample_size": total,
        "success_rate": round((success / total) * 100, 1) if total else 0,
        "workflows": workflows,
        "machines": machines,
        "matrix": matrix_rows,
        "failure_reasons": dict(failure_reasons.most_common()),
    }
