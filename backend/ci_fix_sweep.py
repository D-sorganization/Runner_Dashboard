"""Red-PR sweep logic for event-driven CI-fix dispatch (#2766 items 2 and 5).

Split out of ``ci_fix_dispatch.py`` (which stays under the 500-line file cap). A PR red for
more than ``DEFAULT_SWEEP_RED_MINUTES`` with its lock free gets swept into a fix dispatch even
without a fresh webhook event (hub-unreachable gap); a ``consolidation``-labeled PR gets the
longer ``CONSOLIDATION_SWEEP_RED_MINUTES`` grace period instead, since the existing rule
already keeps its superseded originals open until it merges.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ci_fix_dispatch import DEFAULT_AUDIT_PATH
from ci_fix_locks import GLOBAL_CI_FIX_LOCK_MGR, CIFixLockManager

DEFAULT_SWEEP_RED_MINUTES = 30.0
CONSOLIDATION_SWEEP_RED_MINUTES = 120.0
CONSOLIDATION_LABEL = "consolidation"
STALE_RED_HOURS = 24.0


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def select_sweep_candidates(
    prs: list[dict[str, Any]],
    *,
    now: datetime,
    lock_manager: CIFixLockManager | None = None,
) -> list[dict[str, Any]]:
    """Open, non-draft, armed PRs whose required check has been red long enough, with no lock.

    Pre: each ``pr`` dict names ``repo`` and ``number``; a PR missing ``red_since`` is not red
    and is skipped (DbC: no silent guess at a missing fact).
    Post: every returned PR is unlocked at ``now`` and has been red at least its threshold
    (``CONSOLIDATION_SWEEP_RED_MINUTES`` for a ``consolidation``-labeled PR, else
    ``DEFAULT_SWEEP_RED_MINUTES``); the sweep interval's DbC ("every red armed PR gets a
    decision within one sweep interval") is satisfied by the caller dispatching one for each.
    """
    locks = lock_manager or GLOBAL_CI_FIX_LOCK_MGR
    candidates: list[dict[str, Any]] = []
    for pr in prs:
        if pr.get("is_draft") or not pr.get("auto_merge_armed"):
            continue
        red_since = _parse_iso(pr.get("red_since"))
        if red_since is None:
            continue
        labels = pr.get("labels") or []
        threshold = CONSOLIDATION_SWEEP_RED_MINUTES if CONSOLIDATION_LABEL in labels else DEFAULT_SWEEP_RED_MINUTES
        red_minutes = (now - red_since).total_seconds() / 60.0
        if red_minutes < threshold:
            continue
        repo = str(pr.get("repo") or "")
        number = pr.get("number")
        if not repo or number is None:
            continue
        if locks.is_locked(repo, int(number)):
            continue
        candidates.append(pr)
    return candidates


def count_stale_red_prs_without_fix_attempts(
    prs: list[dict[str, Any]],
    *,
    now: datetime,
    audit_file: Path | None = None,
    hours: float = STALE_RED_HOURS,
) -> int:
    """Count open PRs red for more than ``hours`` with zero CI-fix audit rows ever (#2766 metric).

    Feeds the existing Prometheus exporter (``routers/metrics.py``); the issue's problem
    statement is PRs going red and nobody ever trying a fix (19 of 33 Cursor PRs, Bob 10-23).
    """
    target_path = audit_file or DEFAULT_AUDIT_PATH
    try:
        entries = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        entries = []
    if not isinstance(entries, list):
        entries = []
    attempted = {
        (str(e.get("repository", "")).strip().lower(), e.get("pr_number")) for e in entries if isinstance(e, dict)
    }
    count = 0
    for pr in prs:
        red_since = _parse_iso(pr.get("red_since"))
        if red_since is None:
            continue
        if (now - red_since).total_seconds() / 3600.0 < hours:
            continue
        key = (str(pr.get("repo") or "").strip().lower(), pr.get("number"))
        if key not in attempted:
            count += 1
    return count
