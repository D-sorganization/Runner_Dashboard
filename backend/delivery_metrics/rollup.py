"""Pure rollups over stored PR and check-run facts (#2120)."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from delivery_metrics.escapes import weighted_escapes
from delivery_metrics.models import CheckRunSnapshot, PrSnapshot


@dataclass(frozen=True)
class PrRollup:
    """Per-PR metrics with drill-down ids."""

    repo_full: str
    number: int
    html_url: str
    ci_rounds_to_green: int | None
    lead_time_seconds: float | None
    ci_wait_seconds: float | None
    rework_seconds: float | None
    dieter_wait_seconds: float | None
    churn_flags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_full": self.repo_full,
            "number": self.number,
            "html_url": self.html_url,
            "ci_rounds_to_green": self.ci_rounds_to_green,
            "lead_time_seconds": self.lead_time_seconds,
            "ci_wait_seconds": self.ci_wait_seconds,
            "rework_seconds": self.rework_seconds,
            "dieter_wait_seconds": self.dieter_wait_seconds,
            "churn_flags": list(self.churn_flags),
        }


def _duration_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds())


def ci_rounds_to_green(runs: Sequence[CheckRunSnapshot]) -> int | None:
    """Estimate CI rounds from check-run history on one head SHA."""
    completed = [r for r in runs if r.status == "completed"]
    if not completed:
        return None
    by_name: dict[str, list[CheckRunSnapshot]] = defaultdict(list)
    for run in completed:
        by_name[run.name].append(run)
    rounds = 1
    for items in by_name.values():
        items.sort(key=lambda r: r.completed_at or r.started_at or datetime.min.replace(tzinfo=UTC))
        failures = sum(1 for r in items if r.conclusion == "failure")
        if failures:
            rounds = max(rounds, failures + 1)
    return rounds


def ci_wait_seconds(pr: PrSnapshot, runs: Sequence[CheckRunSnapshot]) -> float | None:
    starts = [r.started_at for r in runs if r.started_at is not None]
    if not starts:
        return None
    first = min(starts)
    return _duration_seconds(pr.created_at, first)


def lead_time_seconds(pr: PrSnapshot) -> float | None:
    end = pr.merged_at or pr.closed_at
    return _duration_seconds(pr.created_at, end)


def _run_sequence_key(run: CheckRunSnapshot) -> datetime:
    return run.started_at or run.completed_at or datetime.min.replace(tzinfo=UTC)


def rework_seconds(runs: Sequence[CheckRunSnapshot]) -> float | None:
    """Seconds waiting to retry after a failed check (per check name).

    Pre: ``runs`` belong to one PR head.
    Post: ``None`` when no runs; otherwise non-negative. In-progress retries count.
    """
    if not runs:
        return None
    by_name: dict[str, list[CheckRunSnapshot]] = defaultdict(list)
    for run in runs:
        by_name[run.name].append(run)
    total = 0.0
    saw_failure = False
    for items in by_name.values():
        items.sort(key=_run_sequence_key)
        for idx, run in enumerate(items):
            if run.status != "completed" or run.conclusion != "failure" or run.completed_at is None:
                continue
            saw_failure = True
            for nxt in items[idx + 1 :]:
                if nxt.started_at is None:
                    continue
                total += max(0.0, (nxt.started_at - run.completed_at).total_seconds())
                break
    return total if saw_failure else 0.0


def churn_flags(pr: PrSnapshot, runs: Sequence[CheckRunSnapshot], *, now: datetime) -> tuple[str, ...]:
    flags: list[str] = []
    if pr.state == "closed" and not pr.merged:
        flags.append("closed_unmerged")
    age = _duration_seconds(pr.created_at, now)
    if pr.state == "open" and age is not None and age > timedelta(days=7).total_seconds():
        flags.append("long_lived_open")
    failures = sum(1 for r in runs if r.conclusion == "failure")
    if failures >= 10:
        flags.append("high_ci_failures")
    rounds = ci_rounds_to_green(runs)
    if rounds is not None and rounds >= 10:
        flags.append("ci_rounds_10_plus")
    return tuple(flags)


def build_pr_rollup(pr: PrSnapshot, runs: Sequence[CheckRunSnapshot], *, now: datetime) -> PrRollup:
    return PrRollup(
        repo_full=pr.repo_full,
        number=pr.number,
        html_url=pr.html_url,
        ci_rounds_to_green=ci_rounds_to_green(runs),
        lead_time_seconds=lead_time_seconds(pr),
        ci_wait_seconds=ci_wait_seconds(pr, runs),
        rework_seconds=rework_seconds(runs),
        dieter_wait_seconds=None,
        churn_flags=churn_flags(pr, runs, now=now),
    )


def top_failing_checks(runs: Sequence[CheckRunSnapshot], *, limit: int = 10) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for run in runs:
        if run.conclusion == "failure":
            counter[run.name] += 1
    return [{"check_name": name, "failures": count} for name, count in counter.most_common(limit)]


def week_start(dt: datetime) -> datetime:
    """Monday 00:00 UTC for the ISO week containing ``dt``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    monday = dt - timedelta(days=dt.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def aggregate_window(
    pulls: Sequence[PrSnapshot],
    checks_by_head: Mapping[str, Sequence[CheckRunSnapshot]],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Summary for one time window (trends, not rankings)."""
    pr_rows: list[PrRollup] = []
    all_runs: list[CheckRunSnapshot] = []
    for pr in pulls:
        runs = checks_by_head.get(pr.head_sha, ())
        all_runs.extend(runs)
        pr_rows.append(build_pr_rollup(pr, runs, now=now))
    merged = sum(1 for p in pulls if p.merged)
    ci_known = [r.ci_rounds_to_green for r in pr_rows if r.ci_rounds_to_green is not None]
    return {
        "prs": len(pulls),
        "merged": merged,
        "closed_unmerged": sum(1 for p in pulls if p.state == "closed" and not p.merged),
        "open": sum(1 for p in pulls if p.state == "open"),
        "median_ci_rounds": _median(ci_known),
        "top_failing_checks": top_failing_checks(all_runs),
        "escapes_weighted": weighted_escapes(pulls, checks_by_head),
        "pr_drill_down": [r.to_dict() for r in pr_rows],
    }


def weekly_trends(
    pulls: Sequence[PrSnapshot],
    checks_by_head: Mapping[str, Sequence[CheckRunSnapshot]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Bucket pulls by ISO week of ``updated_at``."""
    buckets: dict[datetime, list[PrSnapshot]] = defaultdict(list)
    for pr in pulls:
        buckets[week_start(pr.updated_at)].append(pr)
    trends: list[dict[str, Any]] = []
    for week in sorted(buckets):
        subset = buckets[week]
        heads = {p.head_sha for p in subset if p.head_sha}
        sub_checks = {h: checks_by_head.get(h, ()) for h in heads}
        flat: dict[str, Sequence[CheckRunSnapshot]] = {h: sub_checks[h] for h in heads}
        body = aggregate_window(subset, flat, now=now)
        slim = {k: v for k, v in body.items() if k != "pr_drill_down"}
        trends.append({"week_start": week.date().isoformat(), **slim})
    return trends


def _median(values: Sequence[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0
