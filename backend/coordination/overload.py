"""Per-repo swarm overload detection (issue #3293, slice 1 of 4).

Read-only: open/merge rate over rolling 1h/6h windows, open-PR count,
merge-queue depth, an ``overloaded`` flag, merge-queue ruleset settings.
Each sub-source degrades independently into a ``warnings`` entry (mirrors
``coordination.brief.build_fleet_brief``). Deferred to later slices:
drain-mode publishing, PR-consolidation, queue-batching UI (items 2-4).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import gh_client
import pr_inventory
from delivery_metrics.adapters.github import GhClientDeliveryCollector
from delivery_metrics.models import PrSnapshot

log = logging.getLogger("dashboard.coordination.overload")

WINDOW_HOURS: tuple[int, ...] = (1, 6)  #: rolling-rate windows reported
CONSECUTIVE_WINDOWS_THRESHOLD = 3  #: trailing 1h buckets of open>merge before overload fires
DEFAULT_OPEN_COUNT_THRESHOLD = 15  #: open-PR count that alone triggers OVERLOADED

_HISTORY_HOURS = max(max(WINDOW_HOURS), CONSECUTIVE_WINDOWS_THRESHOLD)
_MERGE_QUEUE_RULE_TYPE = "merge_queue"
_MERGE_QUEUE_PARAM_KEYS = (
    "max_entries_to_build",
    "max_entries_to_merge",
    "min_entries_to_merge",
    "min_entries_to_merge_wait_minutes",
    "check_response_timeout_minutes",
)
_MAX_QUEUE_RUNS_SCANNED = 300


def _ensure_repo_slug(repo: str) -> str:
    """Defensive precondition for direct/unit-test callers; the HTTP boundary
    (``routers/overload.py``) already validates the format."""
    if not repo or "/" not in repo:
        raise ValueError(f"repo must be an 'owner/name' slug, got {repo!r}")
    return repo


async def _fetch_open_count(repo: str) -> tuple[int, str | None]:
    try:
        items, err = await pr_inventory.fetch_repo_prs(repo)
    except Exception as exc:  # noqa: BLE001 — orthogonality: one source must not break the rest
        return 0, f"open PR count unavailable: {exc}"
    if err:
        return 0, f"open PR count unavailable: {err}"
    return len(items), None


async def _fetch_recent_prs(repo: str) -> tuple[tuple[PrSnapshot, ...], str | None]:
    since = datetime.now(UTC) - timedelta(hours=_HISTORY_HOURS)
    collector = GhClientDeliveryCollector(concurrency=2)
    try:
        prs = await collector.list_pulls_updated_since(repo, since, max_pages=3)
    except gh_client.GhNotFound:
        return (), f"{repo}: repository not found"
    except Exception as exc:  # noqa: BLE001
        return (), f"PR history unavailable: {exc}"
    return prs, None


def _rate_counts(prs: tuple[PrSnapshot, ...], *, since: datetime) -> tuple[int, int]:
    """Return ``(opened_count, merged_count)`` for events at/after *since*."""
    opened = sum(1 for pr in prs if pr.created_at >= since)
    merged = sum(1 for pr in prs if pr.merged_at is not None and pr.merged_at >= since)
    return opened, merged


def _windows(prs: tuple[PrSnapshot, ...], *, now: datetime) -> dict[str, dict[str, float]]:
    windows: dict[str, dict[str, float]] = {}
    for hours in WINDOW_HOURS:
        opened, merged = _rate_counts(prs, since=now - timedelta(hours=hours))
        windows[f"{hours}h"] = {
            "open_count": opened,
            "merge_count": merged,
            "open_rate_per_hour": round(opened / hours, 3),
            "merge_rate_per_hour": round(merged / hours, 3),
        }
    return windows


def _consecutive_overloaded_buckets(prs: tuple[PrSnapshot, ...], *, now: datetime) -> bool:
    """True when each of the trailing N 1h buckets has open-rate > merge-rate."""
    for bucket in range(CONSECUTIVE_WINDOWS_THRESHOLD):
        bucket_end = now - timedelta(hours=bucket)
        bucket_start = now - timedelta(hours=bucket + 1)
        opened = sum(1 for pr in prs if bucket_start <= pr.created_at < bucket_end)
        merged = sum(1 for pr in prs if pr.merged_at is not None and bucket_start <= pr.merged_at < bucket_end)
        if not (opened > merged):
            return False
    return True


async def _fetch_queue_depth(repo: str) -> tuple[int, str | None]:
    path = f"/repos/{repo}/actions/runs?event=merge_group&status=queued&per_page=100"
    depth = 0
    try:
        async for _run in gh_client.paginate(path):
            depth += 1
            if depth >= _MAX_QUEUE_RUNS_SCANNED:
                break
    except gh_client.GhNotFound:
        return 0, f"{repo}: repository not found"
    except Exception as exc:  # noqa: BLE001
        return 0, f"merge-queue depth unavailable: {exc}"
    return depth, None


async def _fetch_merge_queue_settings(repo: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        summaries = await gh_client.get(f"/repos/{repo}/rulesets")
    except gh_client.GhNotFound:
        return None, f"{repo}: repository not found"
    except Exception as exc:  # noqa: BLE001
        return None, f"merge-queue settings unavailable: {exc}"
    if not isinstance(summaries, list):
        return None, "merge-queue settings unavailable: unexpected rulesets response shape"

    for summary in summaries:
        if not isinstance(summary, dict) or summary.get("enforcement") != "active":
            continue
        ruleset_id = summary.get("id")
        if ruleset_id is None:
            continue
        try:
            ruleset = await gh_client.get(f"/repos/{repo}/rulesets/{ruleset_id}")
        except gh_client.GhClientError:
            continue
        for rule in ruleset.get("rules") or []:
            if rule.get("type") != _MERGE_QUEUE_RULE_TYPE:
                continue
            params = rule.get("parameters") or {}
            return (
                {key: params.get(key) for key in _MERGE_QUEUE_PARAM_KEYS},
                None,
            )
    return None, "no active ruleset defines a merge_queue rule"


async def build_overload_snapshot(
    repo: str, *, open_count_threshold: int = DEFAULT_OPEN_COUNT_THRESHOLD
) -> dict[str, Any]:
    """Pre: ``repo`` is an ``owner/name`` slug, ``open_count_threshold`` > 0.
    Post: result always has ``overloaded``/``overload_reasons``; a failing
    sub-source adds to ``warnings`` instead of raising."""
    _ensure_repo_slug(repo)
    if open_count_threshold <= 0:
        raise ValueError(f"open_count_threshold must be positive, got {open_count_threshold!r}")

    now = datetime.now(UTC)
    warnings: list[str] = []

    open_count, open_err = await _fetch_open_count(repo)
    if open_err:
        warnings.append(open_err)
    recent_prs, history_err = await _fetch_recent_prs(repo)
    if history_err:
        warnings.append(history_err)
    queue_depth, queue_err = await _fetch_queue_depth(repo)
    if queue_err:
        warnings.append(queue_err)
    merge_queue_settings, settings_err = await _fetch_merge_queue_settings(repo)
    if settings_err:
        warnings.append(settings_err)

    reasons: list[str] = []
    if _consecutive_overloaded_buckets(recent_prs, now=now):
        reasons.append(f"open-rate exceeded merge-rate for {CONSECUTIVE_WINDOWS_THRESHOLD} consecutive 1h windows")
    if open_count > open_count_threshold:
        reasons.append(f"open PR count {open_count} exceeds threshold {open_count_threshold}")

    return {
        "repo": repo,
        "generated_at": now.isoformat(),
        "open_pr_count": open_count,
        "windows": _windows(recent_prs, now=now),
        "merge_queue_depth": queue_depth,
        "merge_queue_settings": merge_queue_settings,
        "overloaded": bool(reasons),
        "overload_reasons": reasons,
        "open_count_threshold": open_count_threshold,
        "consecutive_windows_threshold": CONSECUTIVE_WINDOWS_THRESHOLD,
        "warnings": warnings,
    }
