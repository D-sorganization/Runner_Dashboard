"""72-hour open PR rule for lane claims (PD-4 #3819).

Before an agent claims a new issue, any open PR they own that exceeds
``PR_AGE_RULE_HOURS`` must be cleared (merged or closed) or diagnosed.
"""

from __future__ import annotations

import json
import logging
import subprocess  # noqa: S404
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from coordination import roster
from flow_metrics import PR_AGE_RULE_HOURS, open_pr_age_hours
from staff.claim_guard import full_repo

log = logging.getLogger("dashboard.coordination.pr_age_rule")

GH_TIMEOUT_SECONDS = 20
AGENT_LABEL_PREFIX = "agent:"
SyncFetch = Callable[[str], Any]


def _gh_cli(path: str) -> Any:
    proc = subprocess.run(  # noqa: S603
        ["gh", "api", path],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"gh api {path} failed").strip()[:300])
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def _normalize_agent(agent: str) -> str:
    lowered = agent.strip().lower()
    if lowered == "cursor-agent":
        return "cursor"
    return lowered


def _agent_from_pr_labels(pr: Mapping[str, Any], agent_ids: Sequence[str]) -> str | None:
    labels = pr.get("labels")
    if not isinstance(labels, list):
        return None
    matches: set[str] = set()
    for label in labels:
        name = label.get("name") if isinstance(label, dict) else label
        if not isinstance(name, str) or not name.startswith(AGENT_LABEL_PREFIX):
            continue
        candidate = _normalize_agent(name[len(AGENT_LABEL_PREFIX) :])
        if _roster_has(agent_ids, candidate):
            matches.add(candidate)
    if len(matches) == 1:
        return next(iter(matches))
    return None


def _roster_has(agent_ids: Sequence[str], candidate: str) -> bool:
    lowered = {a.lower() for a in agent_ids}
    if candidate in lowered:
        return True
    if candidate == "cursor" and "cursor-agent" in lowered:
        return True
    return False


def _agent_from_branch(branch: str, agent_ids: Sequence[str]) -> str | None:
    if not branch or "/" not in branch:
        return None
    prefix = branch.split("/", 1)[0].lower()
    normalized = _normalize_agent(prefix)
    if _roster_has(agent_ids, normalized):
        return normalized
    return None


def resolve_pr_owner(pr: Mapping[str, Any], *, agent_ids: Sequence[str]) -> str | None:
    """Best-effort lane agent for an open PR."""
    from_labels = _agent_from_pr_labels(pr, agent_ids)
    if from_labels:
        return from_labels
    head = pr.get("head")
    branch = head.get("ref") if isinstance(head, dict) else ""
    return _agent_from_branch(str(branch or ""), agent_ids)


def list_aging_prs_for_agent(
    agent: str,
    *,
    repos: Sequence[str],
    fetch: SyncFetch | None = None,
    rule_hours: float = PR_AGE_RULE_HOURS,
) -> list[dict[str, Any]]:
    """Open PRs owned by ``agent`` older than ``rule_hours`` across ``repos`` (short names)."""
    get = fetch or _gh_cli
    agent_ids = roster.agent_ids()
    target = _normalize_agent(agent)
    hits: list[dict[str, Any]] = []
    for repo_short in repos:
        repo_full = full_repo(repo_short)
        try:
            pulls = get(f"/repos/{repo_full}/pulls?state=open&per_page=100")
        except Exception as exc:  # noqa: BLE001
            log.warning("pr age rule: cannot list pulls for %s: %s", repo_full, exc)
            continue
        for pr in pulls if isinstance(pulls, list) else []:
            if not isinstance(pr, dict):
                continue
            owner = resolve_pr_owner(pr, agent_ids=agent_ids)
            if owner != target:
                continue
            age = open_pr_age_hours(str(pr.get("created_at") or ""))
            if age <= rule_hours:
                continue
            hits.append(
                {
                    "repo": repo_short,
                    "number": pr.get("number"),
                    "url": str(pr.get("html_url") or ""),
                    "age_hours": age,
                    "rule_hours": rule_hours,
                    "action": "ci_diagnosis_or_iterate_or_close",
                }
            )
    hits.sort(key=lambda row: float(row.get("age_hours") or 0), reverse=True)
    return hits


def claim_blocked_by_aging_prs(
    agent: str,
    *,
    repos: Sequence[str] | None = None,
    fetch: SyncFetch | None = None,
) -> dict[str, Any] | None:
    """When non-empty, the agent must clear aging PRs before claiming a new issue."""
    from coordination.board import fleet_repos

    scoped = list(repos or fleet_repos())
    aging = list_aging_prs_for_agent(agent, repos=scoped, fetch=fetch)
    if not aging:
        return None
    first = aging[0]
    return {
        "code": "aging_pr_blocks_claim",
        "message": (
            f"Open PR #{first.get('number')} in {first.get('repo')} is "
            f"{first.get('age_hours')}h old (limit {first.get('rule_hours')}h); run CI diagnosis or "
            "iterate/close before claiming a new issue."
        ),
        "aging_prs": aging,
    }
