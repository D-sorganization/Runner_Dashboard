"""Pure escape detectors for delivery metrics rollups (epic #2117)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from delivery_metrics.models import CheckRunSnapshot, PrSnapshot

# Heaviest first — red-main episodes outweigh reverts and rollout failures.
_WEIGHT_RED_MAIN = 30
_WEIGHT_FAILED_ROLLOUT = 20
_WEIGHT_REVERT = 15

_REVERT_TITLE_RE = re.compile(r"^revert\b", re.IGNORECASE)
_ROLLOUT_CHECK_RE = re.compile(
    r"(auto[-_]?deploy|deploy[-_]?qualified|qualified[-_]?release|rollout|hub[-_]?gate|release[-_]?wait)",
    re.IGNORECASE,
)
_MAINLINE_CI_RE = re.compile(r"(ci[-_\s]standard|quality[-_]?gate|merge[-_\s]?queue|tests\b|main[-_\s]?ci)", re.IGNORECASE)


def _event(
    kind: str,
    weight: int,
    pr: PrSnapshot,
    *,
    check_run_ids: Sequence[int] = (),
) -> dict[str, Any]:
    return {
        "kind": kind,
        "weight": weight,
        "repo_full": pr.repo_full,
        "number": pr.number,
        "html_url": pr.html_url,
        "check_run_ids": list(check_run_ids),
    }


def _is_merged_revert(pr: PrSnapshot) -> bool:
    return pr.merged and bool(_REVERT_TITLE_RE.match(pr.title.strip()))


def _failed_rollout_runs(runs: Sequence[CheckRunSnapshot]) -> list[int]:
    ids: list[int] = []
    for run in runs:
        if run.status != "completed" or run.conclusion != "failure":
            continue
        if _ROLLOUT_CHECK_RE.search(run.name):
            ids.append(run.check_run_id)
    return ids


def _red_main_run_ids(pr: PrSnapshot, runs: Sequence[CheckRunSnapshot]) -> list[int]:
    """Post-merge main CI failures only — not pre-merge PR check retries."""
    if not pr.merged or pr.base_ref != "main" or pr.merged_at is None:
        return []
    merged_at = pr.merged_at
    ids: list[int] = []
    for run in runs:
        if run.status != "completed" or run.conclusion != "failure":
            continue
        if run.completed_at is None or run.completed_at < merged_at:
            continue
        if _MAINLINE_CI_RE.search(run.name):
            ids.append(run.check_run_id)
    return ids


def escape_events_for_pr(pr: PrSnapshot, runs: Sequence[CheckRunSnapshot]) -> tuple[dict[str, Any], ...]:
    """Detect escape episodes for one PR from stored facts only.

    Pre: ``pr`` and ``runs`` refer to the same repo; runs are for ``pr.head_sha``.
    Post: each event includes drill-down ids (PR number, check_run_ids).
    """
    events: list[dict[str, Any]] = []
    red_ids = _red_main_run_ids(pr, runs)
    if red_ids:
        events.append(_event("red_main", _WEIGHT_RED_MAIN, pr, check_run_ids=red_ids))
    rollout_ids = _failed_rollout_runs(runs)
    if rollout_ids:
        events.append(_event("failed_rollout", _WEIGHT_FAILED_ROLLOUT, pr, check_run_ids=rollout_ids))
    if _is_merged_revert(pr):
        events.append(_event("revert", _WEIGHT_REVERT, pr))
    return tuple(events)


def weighted_escapes(
    pulls: Sequence[PrSnapshot],
    checks_by_head: Mapping[str, Sequence[CheckRunSnapshot]],
) -> list[dict[str, Any]]:
    """Aggregate escape events for a window, sorted by weight then repo/number."""
    collected: list[dict[str, Any]] = []
    for pr in pulls:
        runs = checks_by_head.get(pr.head_sha, ())
        collected.extend(escape_events_for_pr(pr, runs))
    collected.sort(key=lambda e: (-int(e["weight"]), e["repo_full"], int(e["number"]), e["kind"]))
    return collected
