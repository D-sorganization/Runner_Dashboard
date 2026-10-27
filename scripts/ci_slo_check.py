#!/usr/bin/env python3
"""CI merge-queue SLO metrics and 60-minute stall alarm (#2896)."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

MERGE_QUEUE_REF_RE = re.compile(
    r"^gh-readonly-queue/[^/]+/pr-(\d+)-([0-9a-f]{7,40})$",
    re.IGNORECASE,
)
HUB_CACHE_KEY = "ci_slo_metrics"
CommandRunner = Callable[[Sequence[str]], Any]
DEFAULT_REPO = "D-sorganization/Runner_Dashboard"


@dataclass(frozen=True)
class SloTargets:
    queued_per_open_pr_max: float = 3.0
    merge_group_wait_p90_max_minutes: float = 5.0
    enqueue_to_merge_p90_max_minutes: float = 30.0
    discarded_merge_group_share_max: float = 0.25
    stall_no_merge_minutes: int = 60


DEFAULT_TARGETS = SloTargets()


@dataclass(frozen=True)
class SloMetrics:
    queued_per_open_pr_peak: float
    merge_group_wait_p90_minutes: float
    enqueue_to_merge_p90_minutes: float
    discarded_merge_group_share: float
    landed_built_share: float
    peak_queued_runs: int
    merge_group_runs: int


@dataclass
class QueueDataset:
    as_of: datetime
    open_pr_count: int
    queue_samples: list[dict[str, Any]]
    merge_queue_entries: list[dict[str, Any]]
    merged_prs: list[dict[str, Any]]
    workflow_runs: list[dict[str, Any]]
    jobs_by_run_id: dict[int, list[dict[str, Any]]]


def _parse_ts(value: str) -> datetime:
    text = (value or "").strip().replace("Z", "+00:00")
    if not text:
        raise ValueError("timestamp required")
    return datetime.fromisoformat(text).astimezone(UTC)


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def parse_merge_queue_ref(head_branch: str) -> tuple[int, str] | None:
    match = MERGE_QUEUE_REF_RE.match((head_branch or "").strip())
    return (int(match.group(1)), match.group(2).lower()) if match else None


def classify_merge_group_run(
    run: Mapping[str, Any],
    merged_sha_by_pr: Mapping[int, str],
) -> Literal["landed", "discarded", "in_flight"]:
    if str(run.get("event")) != "merge_group" or str(run.get("status")) != "completed":
        return "in_flight"
    parsed = parse_merge_queue_ref(str(run.get("head_branch") or ""))
    if not parsed:
        return "in_flight"
    pr_number, sha = parsed
    head_sha = str(run.get("head_sha") or sha).lower()
    merged_sha = merged_sha_by_pr.get(pr_number, "")
    if merged_sha and (merged_sha == head_sha or merged_sha.startswith(head_sha[:12])):
        return "landed"
    if merged_sha or str(run.get("conclusion") or "") in {"failure", "cancelled", "canceled", "timed_out"}:
        return "discarded"
    return "in_flight"


def compute_metrics(dataset: QueueDataset) -> SloMetrics:
    merged_sha_by_pr = {
        int(row["number"]): str(row["merge_commit_sha"]).lower()
        for row in dataset.merged_prs
        if row.get("number") and row.get("merge_commit_sha")
    }
    merge_runs = [r for r in dataset.workflow_runs if str(r.get("event")) == "merge_group"]
    states = [classify_merge_group_run(r, merged_sha_by_pr) for r in merge_runs]
    completed = [s for s in states if s != "in_flight"]
    built = len(completed)
    waits: list[float] = []
    for run in merge_runs:
        created = _parse_ts(str(run["created_at"]))
        jobs = dataset.jobs_by_run_id.get(int(run.get("id", 0))) or []
        started = [j.get("started_at") for j in jobs if j.get("started_at")]
        if started:
            first = min(_parse_ts(str(s)) for s in started)
        elif run.get("updated_at"):
            first = _parse_ts(str(run["updated_at"]))
        else:
            continue
        waits.append(max(0.0, (first - created).total_seconds() / 60.0))
    enqueue_durations = [
        max(0.0, (_parse_ts(str(r["merged_at"])) - _parse_ts(str(r["enqueued_at"]))).total_seconds() / 60.0)
        for r in dataset.merged_prs
        if r.get("merged_at") and r.get("enqueued_at")
    ]
    peak_ratio, peak_queued = 0.0, 0
    for sample in dataset.queue_samples:
        queued = int(sample.get("queued_runs", 0))
        open_prs = int(sample.get("open_prs", dataset.open_pr_count) or dataset.open_pr_count)
        peak_queued = max(peak_queued, queued)
        if open_prs:
            peak_ratio = max(peak_ratio, queued / open_prs)
    return SloMetrics(
        queued_per_open_pr_peak=peak_ratio,
        merge_group_wait_p90_minutes=percentile(waits, 90.0),
        enqueue_to_merge_p90_minutes=percentile(enqueue_durations, 90.0),
        discarded_merge_group_share=(sum(s == "discarded" for s in completed) / built) if built else 0.0,
        landed_built_share=(sum(s == "landed" for s in completed) / built) if built else 0.0,
        peak_queued_runs=peak_queued,
        merge_group_runs=len(merge_runs),
    )


def stall_alarm(dataset: QueueDataset, *, targets: SloTargets | None = None) -> tuple[bool, str]:
    t = targets or DEFAULT_TARGETS
    if not dataset.merge_queue_entries and not any(
        int(s.get("queued_runs", 0)) > 0 for s in dataset.queue_samples
    ):
        return False, "merge queue empty"
    if not dataset.merged_prs:
        return True, f"no merges in lookback; queue size {len(dataset.merge_queue_entries)}"
    latest = max(_parse_ts(str(row["merged_at"])) for row in dataset.merged_prs if row.get("merged_at"))
    idle = dataset.as_of - latest
    if idle >= timedelta(minutes=t.stall_no_merge_minutes):
        return True, f"no merge for {idle.total_seconds() / 60:.0f}m with active queue"
    return False, f"latest merge {idle.total_seconds() / 60:.0f}m ago"


def publish_hub_snapshot(metrics: SloMetrics, *, ok: bool, detail: Mapping[str, Any]) -> None:
    payload = {
        "ok": ok,
        "metrics": asdict(metrics),
        "detail": dict(detail),
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    try:
        from cache_utils import cache_set  # noqa: PLC0415

        cache_set(HUB_CACHE_KEY, payload)
    except Exception:  # pragma: no cover
        return


def gh_error(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)


def _default_runner(cmd: Sequence[str]) -> Any:
    import subprocess

    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def gh_api(
    path: str,
    *,
    runner: CommandRunner = _default_runner,
    params: Mapping[str, str] | None = None,
) -> Any:
    if not (os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or "").strip():
        gh_error("GITHUB_TOKEN (or GH_TOKEN) is required for live GitHub reads")
        raise SystemExit(2)
    cmd = ["gh", "api", path, "--paginate"]
    for key, value in (params or {}).items():
        cmd.extend(["-f", f"{key}={value}"])
    proc = runner(cmd)
    if proc.returncode != 0:
        gh_error((proc.stderr or proc.stdout or "gh api failed").strip())
        raise SystemExit(2)
    chunks = [json.loads(line) for line in (proc.stdout or "").splitlines() if line.strip()]
    if not chunks:
        return []
    if len(chunks) == 1:
        return chunks[0]
    out: list[Any] = []
    for chunk in chunks:
        if isinstance(chunk, list):
            out.extend(chunk)
        elif isinstance(chunk, dict) and "workflow_runs" in chunk:
            out.extend(chunk["workflow_runs"])
    return out


def fetch_live_dataset(repo: str, *, lookback_hours: int = 24, runner: CommandRunner = _default_runner) -> QueueDataset:
    now = datetime.now(UTC)
    created = (now - timedelta(hours=lookback_hours)).isoformat().replace("+00:00", "Z")
    owner, name = repo.split("/", 1)
    open_prs = gh_api(f"repos/{owner}/{name}/pulls", runner=runner, params={"state": "open", "per_page": "100"})
    open_count = len(open_prs) if isinstance(open_prs, list) else 0
    def runs(**params: str) -> list[dict[str, Any]]:
        payload = gh_api(f"repos/{owner}/{name}/actions/runs", runner=runner, params=params)
        return list(payload.get("workflow_runs") or []) if isinstance(payload, dict) else list(payload)

    queued = runs(status="queued", per_page="100")
    merge_list = runs(event="merge_group", per_page="100", created=f">={created}")
    closed = gh_api(
        f"repos/{owner}/{name}/pulls",
        runner=runner,
        params={"state": "closed", "sort": "updated", "direction": "desc", "per_page": "100"},
    )
    merged_prs = [
        {
            "number": pr.get("number"),
            "merged_at": pr.get("merged_at"),
            "merge_commit_sha": pr.get("merge_commit_sha"),
            "enqueued_at": pr.get("updated_at"),
        }
        for pr in (closed if isinstance(closed, list) else [])
        if isinstance(pr, dict) and pr.get("merged_at")
    ]
    sample = {
        "at": now.isoformat().replace("+00:00", "Z"),
        "queued_runs": len(queued),
        "open_prs": open_count,
    }
    return QueueDataset(
        as_of=now,
        open_pr_count=open_count,
        queue_samples=[sample],
        merge_queue_entries=[],
        merged_prs=merged_prs,
        workflow_runs=merge_list,
        jobs_by_run_id={},
    )


def run_check(
    dataset: QueueDataset,
    *,
    mode: Literal["full", "stall"],
    targets: SloTargets | None = None,
    publish: bool = False,
) -> int:
    t = targets or DEFAULT_TARGETS
    metrics = compute_metrics(dataset)
    breaches: list[str] = []
    if metrics.queued_per_open_pr_peak > t.queued_per_open_pr_max:
        breaches.append(f"queued/open PR peak {metrics.queued_per_open_pr_peak:.2f} > {t.queued_per_open_pr_max}")
    if metrics.merge_group_wait_p90_minutes > t.merge_group_wait_p90_max_minutes:
        wait = metrics.merge_group_wait_p90_minutes
        breaches.append(f"merge_group wait p90 {wait:.1f}m > {t.merge_group_wait_p90_max_minutes}m")
    if metrics.enqueue_to_merge_p90_minutes > t.enqueue_to_merge_p90_max_minutes:
        enq = metrics.enqueue_to_merge_p90_minutes
        breaches.append(f"enqueue→merge p90 {enq:.1f}m > {t.enqueue_to_merge_p90_max_minutes}m")
    if metrics.discarded_merge_group_share > t.discarded_merge_group_share_max:
        share = metrics.discarded_merge_group_share * 100
        cap = t.discarded_merge_group_share_max * 100
        breaches.append(f"discarded merge_group {share:.1f}% > {cap:.0f}%")
    alarm, alarm_detail = stall_alarm(dataset, targets=t)
    report = {
        "metrics": asdict(metrics),
        "breaches": breaches,
        "stall_alarm": {"fired": alarm, "detail": alarm_detail},
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if publish:
        publish_hub_snapshot(metrics, ok=not breaches and not (mode == "stall" and alarm), detail=report)
    if mode == "stall" and alarm:
        gh_error(f"CI queue stall alarm: {alarm_detail}")
        return 1
    if mode == "full" and breaches:
        for item in breaches:
            gh_error(f"CI queue SLO breach: {item}")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", DEFAULT_REPO))
    parser.add_argument("--mode", choices=("full", "stall"), default="full")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--publish-hub", action="store_true")
    args = parser.parse_args(argv)
    dataset = fetch_live_dataset(args.repo, lookback_hours=args.lookback_hours)
    return run_check(dataset, mode=args.mode, publish=args.publish_hub)


if __name__ == "__main__":
    raise SystemExit(main())
