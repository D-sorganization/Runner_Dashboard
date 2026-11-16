"""Compare baseline vs trial windows for the resource-shaping go/no-go (#4312)."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WindowSummary:
    total: int
    successes: int
    failures: int
    timeouts: int
    oom_like: int
    median_duration_s: float
    p95_duration_s: float
    interactive_latency_p95_ms: float


@dataclass(frozen=True)
class ComparisonVerdict:
    regression: bool
    reasons: list[str]
    failure_rate_delta_pp: float
    timeout_rate_delta_pp: float
    duration_p95_increase_pct: float
    latency_p95_increase_pct: float


def _parse_ts(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _duration_seconds(run: dict[str, Any]) -> float | None:
    start = _parse_ts(run.get("run_started_at"))
    end = _parse_ts(run.get("updated_at"))
    if start is None or end is None:
        direct = run.get("duration_seconds")
        if isinstance(direct, (int, float)) and direct >= 0:
            return float(direct)
        return None
    return max(0.0, (end - start).total_seconds())


def _is_oom_like(run: dict[str, Any]) -> bool:
    if str(run.get("failure_class") or "").lower() == "oom":
        return True
    text = " ".join(
        str(run.get(k, "")) for k in ("conclusion", "display_title", "message")
    ).lower()
    return "oom" in text or "out of memory" in text or "killed process" in text


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def summarize_workflow_runs(
    runs: list[dict[str, Any]],
    *,
    interactive_latency_ms: list[float] | None = None,
) -> WindowSummary:
    successes = failures = timeouts = oom_like = 0
    durations: list[float] = []
    for run in runs:
        conclusion = str(run.get("conclusion") or "").lower()
        if conclusion == "success":
            successes += 1
        elif conclusion == "timed_out":
            timeouts += 1
        elif conclusion in {"failure", "cancelled", "startup_failure", "action_required"}:
            failures += 1
        dur = _duration_seconds(run)
        if dur is not None:
            durations.append(dur)
        if _is_oom_like(run):
            oom_like += 1
    total = len(runs)
    median_duration = statistics.median(durations) if durations else 0.0
    p95_duration = _percentile(durations, 95.0)
    latency_samples = interactive_latency_ms or []
    latency_p95 = _percentile(latency_samples, 95.0) if latency_samples else 0.0
    return WindowSummary(
        total=total,
        successes=successes,
        failures=failures,
        timeouts=timeouts,
        oom_like=oom_like,
        median_duration_s=float(median_duration),
        p95_duration_s=float(p95_duration),
        interactive_latency_p95_ms=float(latency_p95),
    )


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return 100.0 * count / total


def _pct_increase(baseline: float, trial: float) -> float:
    if baseline <= 0:
        return 0.0 if trial <= 0 else 100.0
    return 100.0 * (trial - baseline) / baseline


def compare_windows(
    baseline: WindowSummary,
    trial: WindowSummary,
    thresholds: dict[str, Any],
) -> ComparisonVerdict:
    reasons: list[str] = []
    b_fail = _rate(baseline.failures, baseline.total)
    t_fail = _rate(trial.failures, trial.total)
    fail_delta = t_fail - b_fail

    b_to = _rate(baseline.timeouts, baseline.total)
    t_to = _rate(trial.timeouts, trial.total)
    to_delta = t_to - b_to

    dur_inc = _pct_increase(baseline.p95_duration_s, trial.p95_duration_s)
    lat_inc = _pct_increase(
        baseline.interactive_latency_p95_ms, trial.interactive_latency_p95_ms
    )

    max_fail = float(thresholds.get("max_failure_rate_delta_pp", 0.5))
    max_to = float(thresholds.get("max_timeout_rate_delta_pp", 0.25))
    max_dur = float(thresholds.get("max_duration_p95_increase_pct", 15.0))
    max_lat = float(thresholds.get("max_interactive_latency_p95_increase_pct", 20.0))
    min_samples = int(thresholds.get("min_samples_per_window", 20))

    if baseline.total < min_samples or trial.total < min_samples:
        reasons.append(
            f"insufficient samples (baseline={baseline.total}, trial={trial.total}, need>={min_samples})"
        )

    if fail_delta > max_fail:
        reasons.append(f"failure rate +{fail_delta:.2f} pp exceeds {max_fail}")
    if to_delta > max_to:
        reasons.append(f"timeout rate +{to_delta:.2f} pp exceeds {max_to}")
    if dur_inc > max_dur:
        reasons.append(f"job duration p95 +{dur_inc:.1f}% exceeds {max_dur}")
    if lat_inc > max_lat and trial.interactive_latency_p95_ms > 0:
        reasons.append(f"interactive latency p95 +{lat_inc:.1f}% exceeds {max_lat}")
    if trial.oom_like > baseline.oom_like:
        reasons.append(
            f"OOM-like failures increased ({baseline.oom_like} -> {trial.oom_like})"
        )

    regression = bool(reasons)
    return ComparisonVerdict(
        regression=regression,
        reasons=reasons,
        failure_rate_delta_pp=fail_delta,
        timeout_rate_delta_pp=to_delta,
        duration_p95_increase_pct=dur_inc,
        latency_p95_increase_pct=lat_inc,
    )


def recommend_go_no_go(verdict: ComparisonVerdict) -> str:
    if any("insufficient samples" in r for r in verdict.reasons):
        return "inconclusive"
    return "no-go" if verdict.regression else "go"


def load_measurement_bundle(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    if not isinstance(data.get("workflow_runs"), list):
        raise ValueError(f"{path}: workflow_runs must be a list")
    return data


def render_go_no_go_report(
    *,
    baseline: WindowSummary,
    trial: WindowSummary,
    verdict: ComparisonVerdict,
    decision: str,
    host_label: str = "",
) -> str:
    host = host_label or "(unspecified host)"
    lines = [
        f"# Resource shaping trial go/no-go ({host})",
        "",
        f"**Decision:** {decision}",
        "",
        "## Baseline window",
        f"- Samples: {baseline.total}",
        f"- Failures: {baseline.failures} ({_rate(baseline.failures, baseline.total):.2f}%)",
        f"- Timeouts: {baseline.timeouts} ({_rate(baseline.timeouts, baseline.total):.2f}%)",
        f"- OOM-like: {baseline.oom_like}",
        f"- Duration p95: {baseline.p95_duration_s:.0f}s",
        f"- Interactive latency p95: {baseline.interactive_latency_p95_ms:.0f}ms",
        "",
        "## Trial window",
        f"- Samples: {trial.total}",
        f"- Failures: {trial.failures} ({_rate(trial.failures, trial.total):.2f}%)",
        f"- Timeouts: {trial.timeouts} ({_rate(trial.timeouts, trial.total):.2f}%)",
        f"- OOM-like: {trial.oom_like}",
        f"- Duration p95: {trial.p95_duration_s:.0f}s",
        f"- Interactive latency p95: {trial.interactive_latency_p95_ms:.0f}ms",
        "",
        "## Deltas",
        f"- Failure rate: {verdict.failure_rate_delta_pp:+.2f} pp",
        f"- Timeout rate: {verdict.timeout_rate_delta_pp:+.2f} pp",
        f"- Duration p95: {verdict.duration_p95_increase_pct:+.1f}%",
        f"- Interactive latency p95: {verdict.latency_p95_increase_pct:+.1f}%",
        "",
    ]
    if verdict.reasons:
        lines.append("## Regression signals")
        for reason in verdict.reasons:
            lines.append(f"- {reason}")
        lines.append("")
    return "\n".join(lines)
