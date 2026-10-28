"""Read-only merge-queue batching visibility and observed group metrics (issue #3293, slice 4).

Surfaces each repo's merge-queue ruleset values (``max_entries_to_build``,
``max_entries_to_merge``, ``min_entries_to_merge``,
``min_entries_to_merge_wait_minutes``, ``check_response_timeout_minutes``)
alongside observed merge-group duration and merges per hour so an operator can
see when a larger group size would help. Strictly read-only: any ruleset change
remains a human action.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import gh_client

log = logging.getLogger("dashboard.coordination.queue_batching")

MERGE_QUEUE_PARAM_KEYS: tuple[str, ...] = (
    "max_entries_to_build",
    "max_entries_to_merge",
    "min_entries_to_merge",
    "min_entries_to_merge_wait_minutes",
    "check_response_timeout_minutes",
)
DEFAULT_MAX_COMPLETED_GROUP_RUNS = 100


def ensure_repo_slug(repo: str) -> str:
    """Defensive precondition for direct/unit-test callers; ensures an ``owner/name`` slug."""
    if not repo or "/" not in repo:
        raise ValueError(f"repo must be an 'owner/name' slug, got {repo!r}")
    return repo


def _parse_iso_utc(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


async def fetch_completed_merge_group_runs(
    repo: str, *, max_runs: int = DEFAULT_MAX_COMPLETED_GROUP_RUNS
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch recent completed ``merge_group`` workflow runs for ``repo`` (read-only)."""
    ensure_repo_slug(repo)
    if max_runs <= 0:
        raise ValueError(f"max_runs must be positive, got {max_runs!r}")

    runs: list[dict[str, Any]] = []
    path = f"/repos/{repo}/actions/runs?event=merge_group&status=completed&per_page=100"
    try:
        async for run in gh_client.paginate(path):
            if isinstance(run, Mapping):
                runs.append(dict(run))
            if len(runs) >= max_runs:
                break
    except gh_client.GhNotFound:
        return [], f"{repo}: repository not found"
    except Exception as exc:  # noqa: BLE001 — orthogonality: sub-source failure must not break snapshot
        return [], f"merge-queue group runs unavailable: {exc}"
    return runs, None


def compute_observed_group_metrics(
    runs: Sequence[Mapping[str, Any]], *, since: datetime | None = None
) -> dict[str, Any]:
    """Compute observed merge-group count and average duration from completed ``merge_group`` runs."""
    group_bounds: dict[str, tuple[datetime, datetime]] = {}
    for idx, run in enumerate(runs):
        if not isinstance(run, Mapping):
            continue
        started = _parse_iso_utc(run.get("run_started_at") or run.get("created_at"))
        finished = _parse_iso_utc(run.get("updated_at"))
        if started is None or finished is None or finished < started:
            continue
        if since is not None and finished < since:
            continue
        raw_sha = run.get("head_sha")
        key = str(raw_sha).strip() if isinstance(raw_sha, str) and raw_sha.strip() else f"run:{run.get('id', idx)}"
        if key in group_bounds:
            prev_start, prev_end = group_bounds[key]
            group_bounds[key] = (min(prev_start, started), max(prev_end, finished))
        else:
            group_bounds[key] = (started, finished)

    durations = [(end - start).total_seconds() for start, end in group_bounds.values() if end >= start]
    count = len(durations)
    if count == 0:
        return {
            "observed_group_count": 0,
            "observed_group_seconds": None,
            "observed_group_minutes": None,
            "estimated_groups_per_hour": None,
        }

    mean_seconds = sum(durations) / count
    mean_minutes = mean_seconds / 60.0
    return {
        "observed_group_count": count,
        "observed_group_seconds": round(mean_seconds, 1),
        "observed_group_minutes": round(mean_minutes, 2),
        "estimated_groups_per_hour": round(3600.0 / mean_seconds, 2) if mean_seconds > 0 else None,
    }


def build_queue_batching_summary(
    *,
    settings: Mapping[str, Any] | None,
    windows: Mapping[str, Mapping[str, float]],
    queue_depth: int,
    completed_runs: Sequence[Mapping[str, Any]] = (),
    since: datetime | None = None,
) -> dict[str, Any]:
    """Combine merge-queue ruleset values, observed group time, and merges/hr into a read-only summary."""
    if isinstance(queue_depth, bool) or not isinstance(queue_depth, int) or queue_depth < 0:
        raise ValueError(f"queue_depth must be a non-negative integer, got {queue_depth!r}")

    normalized_settings: dict[str, Any] | None = (
        {key: settings.get(key) for key in MERGE_QUEUE_PARAM_KEYS} if isinstance(settings, Mapping) else None
    )
    win_1h = windows.get("1h") or {}
    win_6h = windows.get("6h") or {}
    merges_1h = float(win_1h.get("merge_rate_per_hour", 0.0))
    merges_6h = float(win_6h.get("merge_rate_per_hour", 0.0))
    open_rate_1h = float(win_1h.get("open_rate_per_hour", 0.0))

    observed = compute_observed_group_metrics(completed_runs, since=since)
    raw_max_merge = normalized_settings.get("max_entries_to_merge") if normalized_settings else None
    max_merge = int(raw_max_merge) if isinstance(raw_max_merge, int) and not isinstance(raw_max_merge, bool) else None

    larger_recommended = False
    recommendation: str | None = None
    if max_merge is not None and max_merge > 0:
        depth_exceeds = queue_depth > max_merge
        rate_lagging = open_rate_1h > merges_1h and queue_depth >= max_merge
        if depth_exceeds or rate_lagging:
            larger_recommended = True
            group_time_note = (
                f" with {observed['observed_group_minutes']}m average group time"
                if observed["observed_group_minutes"] is not None
                else ""
            )
            recommendation = (
                f"Queue depth ({queue_depth}) or open rate ({open_rate_1h}/h vs {merges_1h}/h merged) "
                f"exceeds max_entries_to_merge ({max_merge}){group_time_note}; "
                "a larger group size would increase throughput (read-only; human ruleset action only)."
            )

    return {
        "read_only": True,
        "settings": normalized_settings,
        "queue_depth": queue_depth,
        "merges_per_hour_1h": merges_1h,
        "merges_per_hour_6h": merges_6h,
        "open_rate_per_hour_1h": open_rate_1h,
        **observed,
        "larger_group_recommended": larger_recommended,
        "recommendation": recommendation,
    }
