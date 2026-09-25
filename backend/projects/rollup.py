"""Fleet rollup over per-repo overviews: priority join, ordering, summary, untracked report.

Pure functions (no I/O) so the Projects list, the fleet-status report and the
fleet-curator worklist all derive from the same overview dicts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from projects.priorities import TIERS, UNRANKED, ProjectPriority, tier_rank, unranked

_STATUS_KEYS = ("planned", "in_progress", "shipped", "parked")


def attach_priorities(
    projects: Sequence[Mapping[str, Any]], priorities: Mapping[str, ProjectPriority]
) -> list[dict[str, Any]]:
    """Copy each overview with a ``priority`` block (``unranked`` when the file omits it)."""
    return [{**p, "priority": priorities.get(p["repo"], unranked()).to_dict()} for p in projects]


def sort_by_priority(projects: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Stable sort by tier: config order is kept within a tier."""
    return sorted((dict(p) for p in projects), key=lambda p: tier_rank(p["priority"]["tier"]))


def _untracked_count(project: Mapping[str, Any]) -> int:
    cov = project.get("coverage")
    return int(cov["untracked_count"]) if isinstance(cov, Mapping) else 0


def fleet_summary(projects: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_tier = dict.fromkeys((*TIERS, UNRANKED), 0)
    for p in projects:
        tier = p["priority"]["tier"]
        by_tier[tier if tier in by_tier else UNRANKED] += 1
    return {
        "repos": len(projects),
        "with_charter": sum(1 for p in projects if p["charter_present"]),
        "without_charter": [p["repo"] for p in projects if not p["charter_present"]],
        "features": {key: sum(int(p["progress"][key]) for p in projects) for key in _STATUS_KEYS},
        "by_tier": by_tier,
        "decisions_needed": sum(len(p.get("decisions_needed") or []) for p in projects),
        "untracked_items": sum(_untracked_count(p) for p in projects),
    }


def untracked_report(projects: Sequence[Mapping[str, Any]], org_repos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The fleet-curator worklist: orphaned open items, charter-less repos, unregistered repos."""
    configured = {p["repo"] for p in projects}
    repos = [
        {
            "repo": p["repo"],
            "tier": p["priority"]["tier"],
            "charter_present": p["charter_present"],
            "untracked_count": _untracked_count(p),
            "untracked": p["coverage"]["untracked"],
        }
        for p in projects
        if _untracked_count(p) > 0
    ]
    return {
        "repos": repos,
        "total_untracked": sum(r["untracked_count"] for r in repos),
        "repos_without_charter": [p["repo"] for p in projects if not p["charter_present"]],
        "unregistered_repos": sorted(
            r["name"] for r in org_repos if not r.get("archived") and r.get("name") not in configured
        ),
    }
