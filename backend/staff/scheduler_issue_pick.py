"""Issue-remediator target selection for the staff scheduler (500-line cap split)."""

from __future__ import annotations

import logging
from datetime import datetime
from collections.abc import Callable
from typing import Any

from coordination import claims as coordination_claims
from staff import claim_guard
from staff.roles import RoleSpec

log = logging.getLogger("dashboard.staff.scheduler")

ISSUE_REMEDIATOR_ROLE = "issue-remediator"
ISSUE_REMEDIATOR_SKIP_LABELS = frozenset(
    {
        "blocked",
        "do-not-automate",
        "judgement:contested",
        "judgement:design",
        "measurement",
        "research",
    }
)
_PRIORITY_LABELS = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def scheduled_repo(repos: tuple[str, ...] | list[str], now: datetime) -> str:
    """Rotate a scheduled role through its repos, one per calendar day (first repo when none rotate)."""
    return repos[now.toordinal() % len(repos)] if repos else ""


def fetch_open_issues(repo: str) -> tuple[list[dict[str, Any]], str | None]:
    """Best-effort open-issue fetch for one repository (pull requests are filtered out)."""
    repo_full = claim_guard.full_repo(repo)
    try:
        payload = claim_guard._gh_cli(f"/repos/{repo_full}/issues?state=open&per_page=100")  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    if not isinstance(payload, list):
        return [], "unexpected response shape"
    issues = [row for row in payload if isinstance(row, dict) and row.get("pull_request") is None]
    return issues, None


def fetch_issue_claim_status(repo: str, issue: int) -> dict[str, Any]:
    """Claim status from coordination scripts; fail-closed reasons are surfaced to callers."""
    try:
        return coordination_claims.check(repo, issue)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "held": None, "reason": f"claim check failed: {exc}"}


def _label_names(issue: dict[str, Any]) -> set[str]:
    labels = issue.get("labels") if isinstance(issue, dict) else []
    names = set()
    for label in labels or []:
        if isinstance(label, dict):
            name = str(label.get("name") or "").strip().lower()
        else:
            name = str(label).strip().lower()
        if name:
            names.add(name)
    return names


def _skip_label_reason(role: str, labels: set[str]) -> str:
    if role != ISSUE_REMEDIATOR_ROLE:
        return ""
    if "do-not-automate" in labels:
        return "skip label: do-not-automate"
    matched = sorted(name for name in labels if name in ISSUE_REMEDIATOR_SKIP_LABELS)
    if matched:
        return f"skip labels: {', '.join(matched)}"
    return ""


def _priority_key(issue: dict[str, Any], labels: set[str]) -> tuple[int, int, int]:
    priority = min((_PRIORITY_LABELS[name] for name in labels if name in _PRIORITY_LABELS), default=2)
    quick_win_penalty = 0 if "quick-win" in labels else 1
    issue_number = int(issue.get("number") or 0)
    return priority, quick_win_penalty, issue_number


def pick_issue_target(
    role: RoleSpec,
    now: datetime,
    *,
    fetch_open: Callable[[str], tuple[list[dict[str, Any]], str | None]] | None = None,
    fetch_claim: Callable[[str, int], dict[str, Any]] | None = None,
) -> tuple[str, int] | tuple[None, None]:
    """Choose the highest-priority unclaimed issue across the role's repos."""
    open_issues = fetch_open or fetch_open_issues
    claim_status = fetch_claim or fetch_issue_claim_status
    repos = tuple(role.repos)
    if not repos:
        return None, None
    start = now.toordinal() % len(repos)
    rotated = list(repos[start:]) + list(repos[:start])
    candidates: list[tuple[tuple[int, int, int], str, int]] = []
    reasons: list[str] = []
    for repo in rotated:
        issues, fetch_error = open_issues(repo)
        if fetch_error:
            reasons.append(f"{repo}: issue lookup failed ({fetch_error})")
            continue
        if not issues:
            reasons.append(f"{repo}: no open issues")
            continue
        repo_reason = "no eligible issues"
        for issue in issues:
            issue_number = int(issue.get("number") or 0)
            if issue_number <= 0:
                continue
            labels = _label_names(issue)
            skip_reason = _skip_label_reason(role.name, labels)
            if skip_reason:
                repo_reason = skip_reason
                continue
            claim = claim_status(repo, issue_number)
            if not claim.get("available", False):
                repo_reason = f"claim check unavailable ({claim.get('reason') or 'unknown'})"
                continue
            if claim.get("held"):
                repo_reason = f"held ({claim.get('reason') or 'claimed elsewhere'})"
                continue
            guard_reason = claim_guard.scheduler_blocker(repo, issue_number, None, role.name)
            if guard_reason:
                repo_reason = guard_reason
                continue
            candidates.append((_priority_key(issue, labels), repo, issue_number))
        if repo_reason:
            reasons.append(f"{repo}: {repo_reason}")
    if candidates:
        _, repo, issue_number = min(candidates, key=lambda row: row[0])
        return repo, issue_number
    joined = "; ".join(reasons[:4]) if reasons else "no candidate repositories"
    log.info("staff scheduler: issue-remediator target selection found no eligible issues (%s)", joined)
    return None, None
