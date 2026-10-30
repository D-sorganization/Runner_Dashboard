"""Fleet Job Board S1: read-only claimable-work listing (issue #2782, epic #2781).

Reuses the manual claim ritual's evidence (``coordination.claims``, #2765's
``staff.claim_guard.closing_pr_for``) instead of a second claim store. Holds (S2)
and claim/release (S3) are later slices; this module only lists, never writes.
"""

from __future__ import annotations

import importlib
import logging
from datetime import UTC, datetime
from typing import Any

from coordination import board as board_mod
from coordination import claims as claims_mod
from staff.claim_guard import closing_pr_for, full_repo

log = logging.getLogger("dashboard.coordination.jobs")

MIN_LIMIT = 1
MAX_LIMIT = 200
HOLD_LABELS = frozenset({"blocked", "do-not-automate", "do-not-merge"})
PRIORITY_TIERS = ("critical", "high", "medium", "low")
_UNRANKED = len(PRIORITY_TIERS)


def _safe_top_priorities(limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    """``priorities.service.top_priorities(limit)``; a missing/failing sibling is a warning, not a 500."""
    try:
        module = importlib.import_module("priorities.service")
    except ImportError:
        return [], []
    loader = getattr(module, "top_priorities", None)
    if loader is None:
        return [], []
    try:
        return list(loader(limit) or []), []
    except Exception as exc:  # noqa: BLE001
        log.warning("jobs: priorities unavailable: %s", exc)
        return [], [f"priorities unavailable: {exc}"]


def _label_names(issue: dict[str, Any]) -> list[str]:
    names = [(lbl.get("name") if isinstance(lbl, dict) else str(lbl)) for lbl in issue.get("labels") or []]
    return [n for n in names if n]


def _priority_tier_and_name(lowered_labels: set[str]) -> tuple[int, str]:
    for tier, name in enumerate(PRIORITY_TIERS):
        if name in lowered_labels:
            return tier, name
    return _UNRANKED, ""


def _board_rank(priorities: list[dict[str, Any]], repo: str, issue_number: int) -> int:
    """Position of the first priority item naming ``repo#issue_number``, else past the end (unranked)."""
    needle = f"#{issue_number}"
    for index, item in enumerate(priorities):
        haystack = " ".join(str(item.get(k) or "") for k in ("item", "scope", "tracking", "project"))
        if needle in haystack and repo.casefold() in haystack.casefold():
            return index
    return len(priorities)


def _excluded(repo: str, issue: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "repo": repo,
        "issue": issue.get("number"),
        "title": str(issue.get("title") or ""),
        "url": str(issue.get("html_url") or ""),
        "reason": reason,
    }


def _closing_pr(repo_full: str, issue_number: int, pulls_by_repo: dict[str, list[Any]]) -> dict[str, Any] | None:
    """An open/merged PR closing ``issue_number``; one pulls fetch per repo."""
    pulls = pulls_by_repo.get(repo_full)
    if pulls is None:
        raw = claims_mod.evidence_fetch(f"/repos/{repo_full}/pulls?state=all&per_page=100")
        candidates = raw if isinstance(raw, list) else []
        pulls = [pr for pr in candidates if pr.get("state") != "closed" or pr.get("merged_at")]
        pulls_by_repo[repo_full] = pulls
    return closing_pr_for(pulls, issue_number)


def _candidates(repo: str, warnings: list[str]) -> tuple[list[dict[str, Any]], bool]:
    """Open issues (PRs excluded) for ``repo``; ``(issues, repo_complete)``."""
    repo_full = full_repo(repo)
    try:
        raw = claims_mod.evidence_fetch(f"/repos/{repo_full}/issues?state=open&per_page=100")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{repo}: issue list unavailable ({exc})")
        return [], False
    if not isinstance(raw, list):
        warnings.append(f"{repo}: issue list returned an invalid payload")
        return [], False
    return [issue for issue in raw if isinstance(issue, dict) and "pull_request" not in issue], True


def list_jobs(*, repo: str | None, limit: int, include_excluded: bool) -> dict[str, Any]:
    """``{available, complete, jobs, excluded?, warnings, generated_at}``; every ``jobs`` entry is open,
    unclaimed and unheld (an unknown lease state excludes it with a warning — unknown is never free)."""
    assert MIN_LIMIT <= limit <= MAX_LIMIT  # noqa: S101 — contract boundary, route already validates
    repos = [repo] if repo else board_mod.fleet_repos()
    warnings: list[str] = []
    jobs: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    pulls_by_repo: dict[str, list[Any]] = {}
    complete = True
    priorities, priority_warnings = _safe_top_priorities(50)
    warnings.extend(priority_warnings)
    for one_repo in repos:
        issues, repo_complete = _candidates(one_repo, warnings)
        complete = complete and repo_complete
        repo_full = full_repo(one_repo)
        for issue in issues:
            number = issue.get("number")
            if not isinstance(number, int):
                continue
            labels = _label_names(issue)
            lowered = {name.strip().lower() for name in labels}
            claim_label = next((name for name in labels if name.lower().startswith("claim:")), None)
            if claim_label:
                excluded.append(_excluded(one_repo, issue, f"carries {claim_label}"))
                continue
            hold_label = next((name for name in lowered if name in HOLD_LABELS), None)
            if hold_label:
                excluded.append(_excluded(one_repo, issue, f"carries hold label '{hold_label}'"))
                continue
            try:
                hit = _closing_pr(repo_full, number, pulls_by_repo)
            except Exception as exc:  # noqa: BLE001
                excluded.append(_excluded(one_repo, issue, f"PR lookup unavailable: {exc}"))
                warnings.append(f"{one_repo}#{number}: PR lookup unavailable ({exc})")
                continue
            if hit is not None:
                excluded.append(_excluded(one_repo, issue, f"open PR #{hit.get('number')} already closes it"))
                continue
            status = claims_mod.check(one_repo, number)
            if not status["available"]:
                excluded.append(_excluded(one_repo, issue, f"claim check unavailable: {status['reason']}"))
                warnings.append(f"{one_repo}#{number}: claim check unavailable ({status['reason']})")
                continue
            if status["held"]:
                excluded.append(_excluded(one_repo, issue, f"claimed by {status.get('agent') or 'another agent'}"))
                continue
            tier, priority_name = _priority_tier_and_name(lowered)
            jobs.append(
                {
                    "repo": one_repo,
                    "issue": number,
                    "title": str(issue.get("title") or ""),
                    "url": str(issue.get("html_url") or ""),
                    "labels": labels,
                    "priority": priority_name,
                    "updated_at": str(issue.get("updated_at") or ""),
                    "_sort": (tier, _board_rank(priorities, one_repo, number), str(issue.get("created_at") or "")),
                }
            )
    jobs.sort(key=lambda j: j["_sort"])
    for job in jobs:
        del job["_sort"]
    result: dict[str, Any] = {
        "available": True,
        "complete": complete,
        "jobs": jobs[:limit],
        "warnings": warnings,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    if include_excluded:
        result["excluded"] = excluded
    return result
