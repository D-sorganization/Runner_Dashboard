"""Single cached fleet board combining briefing, merge-queue, hub status and
per-repo issues/PRs (issue #3314, epic #3295 Grok Bot offload).

Lets a messenger bot relay one call instead of polling ``coordination.briefing``,
the merge queue, fleet status and the org issue/PR inventories itself. Every
part degrades independently (mirrors ``coordination.briefing``): a failing
part adds a warning and never raises.
"""

from __future__ import annotations

from typing import Any

from coordination.briefing import build_briefing
from coordination.service import now_iso
from staff.store import ACTIVE_STATUSES

BRIEF_CACHE_TTL_S = 20.0


async def _queue_heads() -> tuple[dict[str, Any], list[str]]:
    try:
        from routers.queue import _queue_impl  # noqa: PLC0415

        return await _queue_impl(), []
    except Exception as exc:  # noqa: BLE001 — orthogonality: queue failure must not break the brief
        return {}, [f"queue unavailable: {exc}"]


async def _hub_status() -> tuple[dict[str, Any], list[str]]:
    try:
        from fleet_status_cache import fleet_status_cache_key, serve_fleet_status  # noqa: PLC0415
        from routers.fleet import _collect_fleet_status  # noqa: PLC0415

        key = fleet_status_cache_key(exclude_pools=True)

        async def collect() -> dict[str, Any]:
            return await _collect_fleet_status(exclude_pools=True)

        return await serve_fleet_status(key, collect), []
    except Exception as exc:  # noqa: BLE001
        return {}, [f"hub status unavailable: {exc}"]


async def _issues_and_prs(repo: str | None, limit: int) -> tuple[dict[str, Any], list[str]]:
    try:
        from routers import repos as repos_mod  # noqa: PLC0415

        if repo:
            repos = [repo]
        else:
            org_repos = await repos_mod._get_recent_org_repos(limit=50)  # noqa: SLF001
            repos = [r["full_name"] for r in org_repos]
        prs = await repos_mod._pr_inventory.fetch_all_prs(repos, limit=limit)  # noqa: SLF001
        issues = await repos_mod._issue_inventory.fetch_all_issues(repos, limit=limit)  # noqa: SLF001
        return {"prs": prs, "issues": issues}, []
    except Exception as exc:  # noqa: BLE001
        return {}, [f"issues/prs unavailable: {exc}"]


async def build_fleet_brief(repo: str | None, *, limit: int = 100) -> dict[str, Any]:
    """Everything a messenger bot needs to relay in one call."""
    briefing = await build_briefing(repo, agent=None)
    queue, w_queue = await _queue_heads()
    hub_status, w_hub = await _hub_status()
    issues_and_prs, w_issues = await _issues_and_prs(repo, limit)
    return {
        "generated_at": now_iso(),
        "repo": repo,
        "holds": briefing["holds"],
        "staff_runs": briefing["staff_runs"],
        "priorities": briefing["priorities"],
        "queue": queue,
        "hub_status": hub_status,
        **issues_and_prs,
        "warnings": [*briefing["warnings"], *w_queue, *w_hub, *w_issues],
    }


def render_markdown(brief: dict[str, Any]) -> str:
    """Render ``build_fleet_brief``'s output as a short digest a bot can post verbatim."""
    repo = brief.get("repo") or "all repos"
    issues = brief.get("issues") or {}
    prs = brief.get("prs") or {}
    issues_count = len(issues.get("items", [])) if isinstance(issues, dict) else len(issues)
    prs_count = len(prs.get("items", [])) if isinstance(prs, dict) else len(prs)
    queue = brief.get("queue") or {}
    holds = brief.get("holds") or []
    runs = brief.get("staff_runs") or []
    active_runs = [r for r in runs if isinstance(r, dict) and r.get("status") in ACTIVE_STATUSES]
    hub = brief.get("hub_status") or {}
    offline = [name for name, data in hub.items() if isinstance(data, dict) and data.get("status") == "offline"]

    lines = [
        f"# Fleet brief — {repo}",
        f"_generated {brief.get('generated_at', '')}_",
        "",
        f"- Open issues: {issues_count}",
        f"- Open PRs: {prs_count}",
        f"- Merge queue: {queue.get('queued_count', 0)} queued, {queue.get('in_progress_count', 0)} in progress",
        f"- Active holds: {len(holds)}",
        *(f"  - {h.get('text', h)}" for h in holds[:5]),
        f"- Active staff runs: {len(active_runs)}",
        f"- Hub nodes offline: {len(offline)}" + (f" ({', '.join(offline[:5])})" if offline else ""),
    ]
    warnings = brief.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {w}" for w in warnings)
    return "\n".join(lines)
