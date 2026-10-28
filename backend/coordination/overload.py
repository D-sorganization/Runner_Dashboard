"""Per-repo swarm overload detection, auto-DRAIN, PR consolidation, and queue batching (issue #3293)."""

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
from coordination.queue_batching import (
    MERGE_QUEUE_PARAM_KEYS as _MERGE_QUEUE_PARAM_KEYS,
)
from coordination.queue_batching import (
    build_queue_batching_summary,
    fetch_completed_merge_group_runs,
)
from coordination.queue_batching import (
    ensure_repo_slug as _ensure_repo_slug,
)
from delivery_metrics.adapters.github import GhClientDeliveryCollector
from delivery_metrics.models import PrSnapshot

log = logging.getLogger("dashboard.coordination.overload")

WINDOW_HOURS: tuple[int, ...] = (1, 6)  #: rolling-rate windows reported
CONSECUTIVE_WINDOWS_THRESHOLD = 3  #: trailing 1h buckets of open>merge before overload fires
DEFAULT_OPEN_COUNT_THRESHOLD = 15  #: open-PR count that alone triggers OVERLOADED
DEFAULT_PR_SIZE_CAP = 500  #: max combined changed lines (additions + deletions) per consolidated PR
MODE_NORMAL, MODE_DRAIN, RECOVERY_WINDOW_KEY = "NORMAL", "DRAIN", "1h"
DEFAULT_DRAIN_EXCEPTIONS: tuple[str, ...] = ("ci-unblock", "runner-unblock", "security", "work-report")

_HISTORY_HOURS = max(max(WINDOW_HOURS), CONSECUTIVE_WINDOWS_THRESHOLD)
_MERGE_QUEUE_RULE_TYPE = "merge_queue"
_MAX_QUEUE_RUNS_SCANNED = 300
_drain_latched_since: dict[str, str] = {}


async def _fetch_open_prs(repo: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        items, err = await pr_inventory.fetch_repo_prs(repo)
    except Exception as exc:  # noqa: BLE001 — orthogonality: one source must not break the rest
        return [], f"open PR count unavailable: {exc}"
    return (
        ([], f"open PR count unavailable: {err}") if err else ([dict(i) for i in items if isinstance(i, Mapping)], None)
    )


async def _fetch_open_count(repo: str) -> tuple[int, str | None]:
    items, err = await _fetch_open_prs(repo)
    return len(items), err


async def _fetch_recent_prs(repo: str) -> tuple[tuple[PrSnapshot, ...], str | None]:
    since = datetime.now(UTC) - timedelta(hours=_HISTORY_HOURS)
    try:
        return await GhClientDeliveryCollector(concurrency=2).list_pulls_updated_since(repo, since, max_pages=3), None
    except gh_client.GhNotFound:
        return (), f"{repo}: repository not found"
    except Exception as exc:  # noqa: BLE001
        return (), f"PR history unavailable: {exc}"


def _rate_counts(prs: tuple[PrSnapshot, ...], *, since: datetime) -> tuple[int, int]:
    """Return ``(opened_count, merged_count)`` for events at/after *since*."""
    return (
        sum(1 for pr in prs if pr.created_at >= since),
        sum(1 for pr in prs if pr.merged_at is not None and pr.merged_at >= since),
    )


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
        b_end, b_start = now - timedelta(hours=bucket), now - timedelta(hours=bucket + 1)
        opened = sum(1 for pr in prs if b_start <= pr.created_at < b_end)
        merged = sum(1 for pr in prs if pr.merged_at is not None and b_start <= pr.merged_at < b_end)
        if not (opened > merged):
            return False
    return True


async def _fetch_queue_depth(repo: str) -> tuple[int, str | None]:
    depth = 0
    try:
        async for _run in gh_client.paginate(
            f"/repos/{repo}/actions/runs?event=merge_group&status=queued&per_page=100"
        ):
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
        if not isinstance(summary, dict) or summary.get("enforcement") != "active" or summary.get("id") is None:
            continue
        try:
            ruleset = await gh_client.get(f"/repos/{repo}/rulesets/{summary['id']}")
        except gh_client.GhClientError:
            continue
        for rule in ruleset.get("rules") or []:
            if rule.get("type") == _MERGE_QUEUE_RULE_TYPE:
                params = rule.get("parameters") or {}
                return {key: params.get(key) for key in _MERGE_QUEUE_PARAM_KEYS}, None
    return None, "no active ruleset defines a merge_queue rule"


def reset_drain_state(repo: str | None = None) -> None:
    """Clear latched DRAIN state for ``repo`` (or all repos when ``None``)."""
    _drain_latched_since.clear() if repo is None else _drain_latched_since.pop(repo, None)


def resolve_drain_exceptions(exceptions: Sequence[str] | None = None) -> tuple[str, ...]:
    """Resolve and validate open-PR exception categories allowed during DRAIN."""
    if exceptions is not None:
        if not exceptions:
            raise ValueError("drain_exceptions must not be empty when provided")
        raw_items: Sequence[str] = exceptions
    else:
        env_val = os.environ.get("RD_OVERLOAD_DRAIN_EXCEPTIONS", "").strip()
        raw_items = [s.strip() for s in env_val.split(",") if s.strip()] if env_val else DEFAULT_DRAIN_EXCEPTIONS
    normalized: list[str] = []
    for item in raw_items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"drain_exceptions entries must be non-empty strings, got {item!r}")
        if (token := item.strip().lower()) not in normalized:
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
    latched, cleared, active, latched_at, reason = False, False, False, None, ""
    if overloaded:
        latched_at, active = _drain_latched_since.setdefault(repo, now.isoformat()), True
        reason = "; ".join(overload_reasons) or "repo is OVERLOADED"
    elif repo in _drain_latched_since:
        if merge_rate > open_rate:
            _drain_latched_since.pop(repo, None)
            cleared, reason = (
                True,
                f"drain cleared: {RECOVERY_WINDOW_KEY} merge_rate ({merge_rate}/h) > open_rate ({open_rate}/h)",
            )
        else:
            latched_at, active, latched = _drain_latched_since[repo], True, True
            reason = f"holding DRAIN: {RECOVERY_WINDOW_KEY} merge_rate ({merge_rate}/h) <= open_rate ({open_rate}/h)"

    return {
        "active": active,
        "mode": MODE_DRAIN if active else MODE_NORMAL,
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
    payload = {k: snapshot.get(k) for k in ("generated_at", "drain", "lane_notice", "dispatch_directive")} | {
        "repo": repo,
        "overloaded": bool(snapshot.get("overloaded")),
        "mode": snapshot.get("mode", MODE_NORMAL),
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


def _normalize_branch_prefix(head_ref: str) -> str | None:
    """Derive a topic-scoped branch prefix for consolidation grouping."""
    slash_parts = [part.strip().lower() for part in head_ref.strip().split("/") if part.strip()]
    if not slash_parts:
        return None
    tokens = [tok for tok in slash_parts[-1].split("-") if tok]
    if len(tokens) < 2:
        return None
    if tokens[0].isdigit():
        return f"{tokens[0]}-{tokens[1]}"
    alpha_tokens = [tok for tok in tokens if tok.isalpha()]
    if len(alpha_tokens) < 2:
        return None
    stem = f"{alpha_tokens[0]}-{alpha_tokens[1]}"
    return f"{slash_parts[0]}/{stem}" if len(slash_parts) >= 2 else stem


def _normalize_open_pr_for_consolidation(
    raw: Mapping[str, Any], recent_by_number: Mapping[int, PrSnapshot]
) -> dict[str, Any] | None:
    number = raw.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 0:
        return None
    if "changed_lines" in raw and raw["changed_lines"] is not None:
        changed_lines = int(raw["changed_lines"])
    elif "additions" in raw or "deletions" in raw:
        changed_lines = int(raw.get("additions") or 0) + int(raw.get("deletions") or 0)
    elif number in recent_by_number:
        changed_lines = int(recent_by_number[number].additions) + int(recent_by_number[number].deletions)
    else:
        changed_lines = 0
    if changed_lines < 0:
        raise ValueError(f"changed_lines must be non-negative for PR #{number}, got {changed_lines!r}")
    head_ref = str(raw.get("head_ref") or raw.get("headRefName") or raw.get("branch") or "").strip()
    raw_issues = raw.get("linked_issues")
    if isinstance(raw_issues, Sequence) and not isinstance(raw_issues, (str, bytes)):
        linked_issues = sorted({int(i) for i in raw_issues if isinstance(i, int) and not isinstance(i, bool)})
    else:
        linked_issues = pr_inventory._parse_linked_issues(raw.get("body"))
    raw_paths = raw.get("changed_paths") or raw.get("paths") or raw.get("files") or ()
    return {
        "number": number,
        "title": str(raw.get("title") or ""),
        "head_ref": head_ref,
        "branch_prefix": _normalize_branch_prefix(head_ref),
        "linked_issues": linked_issues,
        "changed_paths": sorted({str(p).strip() for p in raw_paths if isinstance(p, str) and str(p).strip()}),
        "changed_lines": changed_lines,
        "draft": bool(raw.get("draft", False)),
    }


def _build_consolidation_suggestion(
    group_by: str,
    group_key: str,
    surviving: dict[str, Any],
    absorbed: list[dict[str, Any]],
    skipped_over_cap: list[int],
    combined_lines: int,
    size_cap: int,
) -> dict[str, Any]:
    chosen = [surviving, *absorbed]
    shared_issues = sorted(
        {i for p in chosen for i in p["linked_issues"] if sum(i in q["linked_issues"] for q in chosen) >= 2}
    )
    shared_paths = sorted(
        {f for p in chosen for f in p["changed_paths"] if sum(f in q["changed_paths"] for q in chosen) >= 2}
    )
    shared_prefixes = sorted(
        {
            str(p["branch_prefix"])
            for p in chosen
            if p["branch_prefix"] and sum(q["branch_prefix"] == p["branch_prefix"] for q in chosen) >= 2
        }
    )
    absorbed_nums = [p["number"] for p in absorbed]
    return {
        "group_by": group_by,
        "group_key": group_key,
        "reasons": [f"linked_issue:#{i}" for i in shared_issues]
        + [f"branch_prefix:{p}" for p in shared_prefixes]
        + [f"overlapping_paths:{p}" for p in shared_paths],
        "surviving_pr": surviving["number"],
        "absorbed_prs": absorbed_nums,
        "skipped_over_cap_prs": skipped_over_cap,
        "combined_changed_lines": combined_lines,
        "size_cap": size_cap,
        "shared_issues": shared_issues,
        "shared_paths": shared_paths,
        "branch_prefix": shared_prefixes[0] if shared_prefixes else surviving["branch_prefix"],
        "auto_close": False,
        "action": (
            f"Keep #{surviving['number']}; manually close "
            f"{', '.join(f'#{n}' for n in absorbed_nums)} as absorbed into #{surviving['number']}"
        ),
    }


def suggest_pr_consolidations(
    open_prs: Sequence[Mapping[str, Any]],
    *,
    recent_prs: Sequence[PrSnapshot] = (),
    size_cap: int = DEFAULT_PR_SIZE_CAP,
) -> list[dict[str, Any]]:
    """Suggest surviving + absorbed open PR groups under ``size_cap`` (read-only)."""
    if size_cap <= 0:
        raise ValueError(f"size_cap must be positive, got {size_cap!r}")
    recent_by_number = {pr.number: pr for pr in recent_prs}
    normalized = [
        norm
        for item in open_prs
        if isinstance(item, Mapping) and (norm := _normalize_open_pr_for_consolidation(item, recent_by_number))
    ]
    by_issue: dict[int, list[dict[str, Any]]] = {}
    by_prefix: dict[str, list[dict[str, Any]]] = {}
    by_path: dict[str, list[dict[str, Any]]] = {}
    for pr in normalized:
        for issue_num in pr["linked_issues"]:
            by_issue.setdefault(issue_num, []).append(pr)
        if pr["branch_prefix"]:
            by_prefix.setdefault(pr["branch_prefix"], []).append(pr)
        for path in pr["changed_paths"]:
            by_path.setdefault(path, []).append(pr)

    candidate_groups = (
        [("linked_issue", f"#{k}", by_issue[k]) for k in sorted(by_issue) if len(by_issue[k]) >= 2]
        + [("branch_prefix", k, by_prefix[k]) for k in sorted(by_prefix) if len(by_prefix[k]) >= 2]
        + [("overlapping_paths", k, by_path[k]) for k in sorted(by_path) if len(by_path[k]) >= 2]
    )
    assigned_prs: set[int] = set()
    suggestions: list[dict[str, Any]] = []
    for group_by, group_key, members in candidate_groups:
        available = sorted(
            (m for m in members if m["number"] not in assigned_prs), key=lambda m: (m["draft"], m["number"])
        )
        surviving = (
            next((m for m in available if m["changed_lines"] <= size_cap), None) if len(available) >= 2 else None
        )
        if surviving is None:
            continue
        combined_lines, absorbed, skipped_over_cap = surviving["changed_lines"], [], []
        for cand in available:
            if cand["number"] == surviving["number"]:
                continue
            if combined_lines + cand["changed_lines"] <= size_cap:
                absorbed.append(cand)
                combined_lines += cand["changed_lines"]
            else:
                skipped_over_cap.append(cand["number"])
        if absorbed:
            assigned_prs.update(pr["number"] for pr in (surviving, *absorbed))
            suggestions.append(
                _build_consolidation_suggestion(
                    group_by, group_key, surviving, absorbed, skipped_over_cap, combined_lines, size_cap
                )
            )
    return suggestions


async def build_overload_snapshot(
    repo: str,
    *,
    open_count_threshold: int = DEFAULT_OPEN_COUNT_THRESHOLD,
    drain_exceptions: Sequence[str] | None = None,
    pr_size_cap: int = DEFAULT_PR_SIZE_CAP,
) -> dict[str, Any]:
    """Assemble per-repo overload, auto-DRAIN, and PR consolidation snapshot."""
    _ensure_repo_slug(repo)
    if open_count_threshold <= 0:
        raise ValueError(f"open_count_threshold must be positive, got {open_count_threshold!r}")
    if pr_size_cap <= 0:
        raise ValueError(f"pr_size_cap must be positive, got {pr_size_cap!r}")
    resolved_exceptions = resolve_drain_exceptions(drain_exceptions)

    now = datetime.now(UTC)
    open_prs, open_err = await _fetch_open_prs(repo)
    open_count = len(open_prs)
    recent_prs, history_err = await _fetch_recent_prs(repo)
    queue_depth, queue_err = await _fetch_queue_depth(repo)
    merge_queue_settings, settings_err = await _fetch_merge_queue_settings(repo)
    completed_group_runs, group_runs_err = await fetch_completed_merge_group_runs(repo)
    warnings = [err for err in (open_err, history_err, queue_err, settings_err, group_runs_err) if err]

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
    consolidation_suggestions = suggest_pr_consolidations(open_prs, recent_prs=recent_prs, size_cap=pr_size_cap)
    queue_batching = build_queue_batching_summary(
        settings=merge_queue_settings,
        windows=win,
        queue_depth=queue_depth,
        completed_runs=completed_group_runs,
        since=now - timedelta(hours=_HISTORY_HOURS),
    )

    return {
        "repo": repo,
        "generated_at": now.isoformat(),
        "open_pr_count": open_count,
        "windows": win,
        "merge_queue_depth": queue_depth,
        "merge_queue_settings": merge_queue_settings,
        "queue_batching": queue_batching,
        "overloaded": overloaded,
        "overload_reasons": reasons,
        "mode": drain_state["mode"],
        "drain": drain_state,
        "lane_notice": notice_line,
        "dispatch_directive": dispatch_directive,
        "consolidation_suggestions": consolidation_suggestions,
        "pr_size_cap": pr_size_cap,
        "open_count_threshold": open_count_threshold,
        "consecutive_windows_threshold": CONSECUTIVE_WINDOWS_THRESHOLD,
        "warnings": warnings,
    }
