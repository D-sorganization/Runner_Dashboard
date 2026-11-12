"""Derive GitHub Actions workflow run drill-down from check runs (#2117 gap 4)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from delivery_metrics.models import CheckRunSnapshot

_ACTIONS_RUN_RE = re.compile(r"/actions/runs/(\d+)")


def workflow_run_id_from_url(url: str) -> int | None:
    """Extract workflow run id from a GitHub Actions job/check URL."""
    match = _ACTIONS_RUN_RE.search(url)
    if not match:
        return None
    return int(match.group(1))


def workflow_run_html_url(repo_full: str, workflow_run_id: int) -> str:
    return f"https://github.com/{repo_full}/actions/runs/{workflow_run_id}"


def build_workflow_run_drill_down(runs: Sequence[CheckRunSnapshot]) -> list[dict[str, Any]]:
    """Group check runs by Actions workflow run id for drill-down."""
    buckets: dict[int, dict[str, Any]] = {}
    for run in runs:
        wid = run.workflow_run_id
        if wid is None:
            wid = workflow_run_id_from_url(run.details_url)
        if wid is None:
            continue
        bucket = buckets.get(wid)
        if bucket is None:
            bucket = {
                "workflow_run_id": wid,
                "repo_full": run.repo_full,
                "html_url": workflow_run_html_url(run.repo_full, wid),
                "jobs": [],
            }
            buckets[wid] = bucket
        bucket["jobs"].append(
            {
                "check_run_id": run.check_run_id,
                "name": run.name,
                "status": run.status,
                "conclusion": run.conclusion,
                "details_url": run.details_url,
            }
        )
    return [buckets[k] for k in sorted(buckets)]
