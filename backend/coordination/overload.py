"""Per-repo swarm overload detection and auto-DRAIN mode (issue #3293, slices 1-2).

Computes open/merge rate over rolling 1h/6h windows, open-PR count,
merge-queue depth, an ``overloaded`` flag, merge-queue ruleset settings,
and auto-DRAIN mode with recovery hysteresis, configurable open-PR
exceptions, lane-notice lines, and state-file helpers for ``rd-lane.sh``.
Each GitHub sub-source degrades independently into a ``warnings`` entry.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import gh_client
import pr_inventory
from delivery_metrics.adapters.github import GhClientDeliveryCollector
from delivery_metrics.models import PrSnapshot

log = logging.getLogger("dashboard.coordination.overload")

WINDOW_HOURS: tuple[int, ...] = (1, 6)  #: rolling-rate windows reported
CONSECUTIVE_WINDOWS_THRESHOLD = 3  #: trailing 1h buckets of open>merge before overload fires
DEFAULT_OPEN_COUNT_THRESHOLD = 15  #: open-PR count that alone triggers OVERLOADED
MODE_NORMAL = "NORMAL"
MODE_DRAIN = "DRAIN"
RECOVERY_WINDOW_KEY = "1h"
DEFAULT_DRAIN_EXCEPTIONS: tuple[str, ...] = (
    "ci-unblock",
    "runner-unblock",
    "security",
    "work-report",
)

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
_drain_latched_since: dict[str, str] = {}


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


def reset_drain_state(repo: str | None = None) -> None:
    """Clear latched DRAIN state for ``repo`` (or all repos when ``None``)."""
    if repo is None:
        _drain_latched_since.clear()
    else:
        _drain_latched_since.pop(repo, None)


def resolve_drain_exceptions(exceptions: Sequence[str] | None = None) -> tuple[str, ...]:
    """Resolve and validate open-PR exception categories allowed during DRAIN."""
    raw_items: Sequence[str]
    if exceptions is not None:
        if not exceptions:
            raise ValueError("drain_exceptions must not be empty when provided")
        raw_items = exceptions
    else:
        env_val = os.environ.get("RD_OVERLOAD_DRAIN_EXCEPTIONS", "").strip()
        raw_items = (
            [item.strip() for item in env_val.split(",") if item.strip()] if env_val else DEFAULT_DRAIN_EXCEPTIONS
        )

    normalized: list[str] = []
    for item in raw_items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"drain_exceptions entries must be non-empty strings, got {item!r}")
        token = item.strip().lower()
        if token not in normalized:
            normalized.append(token)
    return tuple(normalized)


def format_drain_notice(repo: str, drain_state: Mapping[str, Any]) -> str:
    """Render a single-line notice readable by ``rd-lane.sh`` without credentials."""
    _ensure_repo_slug(repo)
    if not drain_state.get("active"):
        return f"OVERLOAD_MODE={MODE_NORMAL} repo={repo}"
    exceptions = ",".join(drain_state.get("open_pr_exceptions") or DEFAULT_DRAIN_EXCEPTIONS)
    reason = str(drain_state.get("reason") or "repo is OVERLOADED")
    return (
        f"OVERLOAD_MODE={MODE_DRAIN} repo={repo} action=rebase,fix,arm-only open_pr_exceptions={exceptions} ({reason})"
    )


def evaluate_drain_mode(
    repo: str,
    *,
    overloaded: bool,
    overload_reasons: Sequence[str],
    windows: Mapping[str, Mapping[str, float]],
    now: datetime,
    drain_exceptions: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Evaluate auto-DRAIN mode with full-window recovery hysteresis."""
    _ensure_repo_slug(repo)
    exceptions = resolve_drain_exceptions(drain_exceptions)
    recovery = windows.get(RECOVERY_WINDOW_KEY) or {}
    open_rate = float(recovery.get("open_rate_per_hour", 0.0))
    merge_rate = float(recovery.get("merge_rate_per_hour", 0.0))
    recovered = merge_rate > open_rate

    latched = False
    cleared = False
    if overloaded:
        latched_at = _drain_latched_since.setdefault(repo, now.isoformat())
        active = True
        reason = "; ".join(overload_reasons) or "repo is OVERLOADED"
    elif repo in _drain_latched_since:
        if recovered:
            _drain_latched_since.pop(repo, None)
            latched_at = None
            active = False
            cleared = True
            reason = f"drain cleared: {RECOVERY_WINDOW_KEY} merge_rate ({merge_rate}/h) > open_rate ({open_rate}/h)"
        else:
            latched_at = _drain_latched_since[repo]
            active = True
            latched = True
            reason = (
                f"holding DRAIN until {RECOVERY_WINDOW_KEY} merge_rate ({merge_rate}/h) "
                f"exceeds open_rate ({open_rate}/h)"
            )
    else:
        latched_at = None
        active = False
        reason = ""

    mode = MODE_DRAIN if active else MODE_NORMAL
    return {
        "active": active,
        "mode": mode,
        "latched": latched,
        "cleared": cleared,
        "latched_at": latched_at,
        "reason": reason,
        "recovery_window": RECOVERY_WINDOW_KEY,
        "recovery_open_rate_per_hour": open_rate,
        "recovery_merge_rate_per_hour": merge_rate,
        "open_pr_exceptions": list(exceptions),
    }


def is_drain_open_pr_allowed(snapshot: Mapping[str, Any], *, category: str | None = None) -> bool:
    """Return True if opening a new PR is permitted under the snapshot's DRAIN state."""
    drain = snapshot.get("drain")
    active = bool(drain.get("active")) if isinstance(drain, Mapping) else snapshot.get("mode") == MODE_DRAIN
    if not active:
        return True
    if not category or not category.strip():
        return False
    allowed = (drain.get("open_pr_exceptions") if isinstance(drain, Mapping) else None) or DEFAULT_DRAIN_EXCEPTIONS
    return category.strip().lower() in {str(item).strip().lower() for item in allowed}


def write_drain_state_file(path: Path | str, snapshot: Mapping[str, Any]) -> Path:
    """Persist drain state JSON so ``rd-lane.sh`` can inspect it without credentials."""
    target = Path(path)
    repo = str(snapshot.get("repo") or "")
    _ensure_repo_slug(repo)
    payload = {
        "repo": repo,
        "generated_at": snapshot.get("generated_at"),
        "overloaded": bool(snapshot.get("overloaded")),
        "mode": snapshot.get("mode", MODE_NORMAL),
        "drain": snapshot.get("drain"),
        "lane_notice": snapshot.get("lane_notice"),
        "dispatch_directive": snapshot.get("dispatch_directive"),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def read_drain_state_file(path: Path | str) -> dict[str, Any]:
    """Read a persisted drain state JSON file written by :func:`write_drain_state_file`."""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"drain state file not found: {target}")
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "repo" not in data or "mode" not in data:
        raise ValueError(f"invalid drain state payload in {target}")
    return data


async def build_overload_snapshot(
    repo: str,
    *,
    open_count_threshold: int = DEFAULT_OPEN_COUNT_THRESHOLD,
    drain_exceptions: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Pre: ``repo`` is an ``owner/name`` slug, ``open_count_threshold`` > 0.
    Post: result always has ``overloaded``/``overload_reasons`` and ``drain``;
    a failing sub-source adds to ``warnings`` instead of raising."""
    _ensure_repo_slug(repo)
    if open_count_threshold <= 0:
        raise ValueError(f"open_count_threshold must be positive, got {open_count_threshold!r}")
    resolved_exceptions = resolve_drain_exceptions(drain_exceptions)

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

    overloaded = bool(reasons)
    win = _windows(recent_prs, now=now)
    drain_state = evaluate_drain_mode(
        repo,
        overloaded=overloaded,
        overload_reasons=reasons,
        windows=win,
        now=now,
        drain_exceptions=resolved_exceptions,
    )
    notice_line = format_drain_notice(repo, drain_state)
    drain_state["notice_line"] = notice_line
    dispatch_directive = {
        "mode": drain_state["mode"],
        "allow_new_prs": not drain_state["active"],
        "allowed_actions": ["rebase", "fix", "arm"] if drain_state["active"] else ["open", "rebase", "fix", "arm"],
        "open_pr_exceptions": list(resolved_exceptions),
        "notice_line": notice_line,
    }

    return {
        "repo": repo,
        "generated_at": now.isoformat(),
        "open_pr_count": open_count,
        "windows": win,
        "merge_queue_depth": queue_depth,
        "merge_queue_settings": merge_queue_settings,
        "overloaded": overloaded,
        "overload_reasons": reasons,
        "mode": drain_state["mode"],
        "drain": drain_state,
        "lane_notice": notice_line,
        "dispatch_directive": dispatch_directive,
        "open_count_threshold": open_count_threshold,
        "consecutive_windows_threshold": CONSECUTIVE_WINDOWS_THRESHOLD,
        "warnings": warnings,
    }
