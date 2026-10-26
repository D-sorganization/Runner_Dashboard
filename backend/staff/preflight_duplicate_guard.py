"""Pre-dispatch open-PR and active-run guards for staff issue runs (#2254)."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import HTTPException
from staff.runner import get_runner

log = logging.getLogger("dashboard.staff.preflight")


def _issue_branch_pattern(issue_number: int) -> re.Pattern[str]:
    issue_str = str(issue_number)
    return re.compile(rf"(?:^|/|-|_){issue_str}(?:-|_|\b)", re.IGNORECASE)


def _lane_key_for_branch(branch: str, issue_number: int) -> str:
    """Stable lane key up to and including the issue token."""
    pattern = _issue_branch_pattern(issue_number)
    match = pattern.search(branch)
    if not match:
        return branch.lower()
    return branch[: match.end()].rstrip("-_/").lower()


def is_issue_referenced_in_pr(pr: dict[str, Any], issue_number: int) -> bool:
    """Check if an open PR references issue_number via title, branch or body."""
    issue_str = str(issue_number)
    title = str(pr.get("title") or "")
    body = str(pr.get("body") or "")
    head_ref = str((pr.get("head") or {}).get("ref") or "")

    # 1. Branch name pattern: issue-<N>, <N>-, fix/<N>-, feat/<N>-, <N>_
    branch_pattern = _issue_branch_pattern(issue_number)
    if branch_pattern.search(head_ref):
        return True

    # 2. Issue reference in title or body: #<N>, issue #<N>, RM #<N>, closes/fixes #<N>
    ref_patterns = [
        re.compile(rf"#\s*{issue_str}\b"),
        re.compile(rf"issue[-_\s]*#?\s*{issue_str}\b", re.IGNORECASE),
        re.compile(rf"(?:closes|fixes|refs|resolves|remediates)[^\d]*#?\s*{issue_str}\b", re.IGNORECASE),
    ]
    haystack = f"{title}\n{body}"
    return any(p.search(haystack) for p in ref_patterns)


async def check_duplicate_pr_or_run(
    repo: str,
    issue: int,
    *,
    current_run_id: str = "",
    gh_get_fn: Any = None,
    check_open_pr: bool = True,
    check_merged_pr: bool = True,
    check_issue_branches: bool = True,
    check_claim: bool = True,
) -> None:
    """Refuse dispatch if a run, claim, PR, or branch already owns (repo, issue)."""
    # 1. Active staff runs in local runner store (stored refs are ``#N``; compare on the bare number)
    try:
        runner = get_runner()
        if runner and runner.store:
            for r in runner.store.active_runs():
                if r.id == current_run_id:
                    continue
                if r.repo == repo and r.target_kind == "issue" and r.target_ref.lstrip("#") == str(issue):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "active_run_exists",
                            "message": (
                                f"Active staff run '{r.id}' ({r.role}) already targets "
                                f"{repo}#{issue} (pass force_duplicate=true to override)."
                            ),
                            "run_id": r.id,
                            "role": r.role,
                        },
                    )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not query active runs for duplicate guard: %s", exc)

    if not (check_open_pr or check_merged_pr or check_issue_branches or check_claim):
        return

    # 2. Claim / PR / branch ownership checks
    from dispatch_premise import _normalize_repo

    full_repo = _normalize_repo(repo)

    async def _gh_get(endpoint: str) -> Any:
        if gh_get_fn:
            return await gh_get_fn(endpoint)
        try:
            from gh_utils import gh_api  # noqa: PLC0415

            return await gh_api(endpoint)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to query %s: %s", endpoint, exc)
            return None

    if check_claim:
        issue_payload = await _gh_get(f"/repos/{full_repo}/issues/{issue}")
        labels = issue_payload.get("labels") if isinstance(issue_payload, dict) else []
        claim_labels = []
        for lbl in labels or []:
            name = lbl.get("name") if isinstance(lbl, dict) else str(lbl)
            if name and name.startswith("claim:"):
                claim_labels.append(name)
        if claim_labels:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "active_claim_exists",
                    "message": (
                        f"Issue {repo}#{issue} already has active claim label(s): {', '.join(claim_labels)}. "
                        "Do not launch another issue run."
                    ),
                    "claim_labels": claim_labels,
                },
            )

    if check_open_pr:
        open_pulls_raw = await _gh_get(f"/repos/{full_repo}/pulls?state=open&per_page=100")
        open_pulls = open_pulls_raw if isinstance(open_pulls_raw, list) else []
        matching_open = [pr for pr in open_pulls if is_issue_referenced_in_pr(pr, issue)]
        lane_counts: dict[str, int] = {}
        for pr in matching_open:
            branch = str((pr.get("head") or {}).get("ref") or "")
            lane_key = _lane_key_for_branch(branch, issue)
            lane_counts[lane_key] = lane_counts.get(lane_key, 0) + 1
        duplicate_lane = next((lane for lane, count in lane_counts.items() if count > 1), "")
        if matching_open:
            pr = matching_open[0]
            pr_num = pr.get("number")
            pr_url = pr.get("html_url") or pr.get("url") or f"https://github.com/{full_repo}/pull/{pr_num}"
            detail = {
                "code": "open_pr_exists",
                "message": (
                    f"Open PR #{pr_num} ({pr_url}) already targets {repo}#{issue}; "
                    f"dispatch 'pr-remediator' with pr={pr_num} instead (or pass force_duplicate=true)."
                ),
                "pr": pr_num,
                "url": pr_url,
            }
            if duplicate_lane:
                detail["code"] = "lane_pr_exists"
                detail["lane"] = duplicate_lane
                detail["message"] = (
                    f"Lane '{duplicate_lane}' already has an open PR targeting {repo}#{issue}; "
                    "launch gate allows one PR per lane."
                )
            raise HTTPException(status_code=409, detail=detail)

    if check_merged_pr:
        closed_pulls_raw = await _gh_get(f"/repos/{full_repo}/pulls?state=closed&per_page=100")
        closed_pulls = closed_pulls_raw if isinstance(closed_pulls_raw, list) else []
        merged_hit = next(
            (pr for pr in closed_pulls if pr.get("merged_at") and is_issue_referenced_in_pr(pr, issue)),
            None,
        )
        if isinstance(merged_hit, dict):
            pr_num = merged_hit.get("number")
            pr_url = (
                merged_hit.get("html_url") or merged_hit.get("url") or f"https://github.com/{full_repo}/pull/{pr_num}"
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "merged_pr_exists",
                    "message": (
                        f"Merged PR #{pr_num} ({pr_url}) already references {repo}#{issue}; "
                        "open a follow-up issue/PR lane instead of launching a duplicate issue run."
                    ),
                    "pr": pr_num,
                    "url": pr_url,
                },
            )

    if check_issue_branches:
        branches_raw = await _gh_get(f"/repos/{full_repo}/branches?per_page=100")
        branches = branches_raw if isinstance(branches_raw, list) else []
        pattern = _issue_branch_pattern(issue)
        matching_branches = [
            str(branch.get("name") or "")
            for branch in branches
            if isinstance(branch, dict) and pattern.search(str(branch.get("name") or ""))
        ]
        if matching_branches:
            lane_counts = {}
            for branch_name in matching_branches:
                lane_key = _lane_key_for_branch(branch_name, issue)
                lane_counts[lane_key] = lane_counts.get(lane_key, 0) + 1
            duplicate_lane = next((lane for lane, count in lane_counts.items() if count > 1), "")
            example = matching_branches[0]
            message = f"Existing branch '{example}' already targets {repo}#{issue}; do not launch another issue run."
            detail = {
                "code": "issue_branch_exists",
                "message": message,
                "branch": example,
            }
            if duplicate_lane:
                detail["code"] = "lane_branch_exists"
                detail["lane"] = duplicate_lane
                detail["message"] = (
                    f"Lane '{duplicate_lane}' already has branch(es) for {repo}#{issue}; "
                    "launch gate allows one PR lane per issue."
                )
            raise HTTPException(status_code=409, detail=detail)
