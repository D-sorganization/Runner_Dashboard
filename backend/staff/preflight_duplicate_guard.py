"""Pre-dispatch open-PR and active-run guards for staff issue runs (#2254)."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import HTTPException
from staff.runner import get_runner

log = logging.getLogger("dashboard.staff.preflight")


def is_issue_referenced_in_pr(pr: dict[str, Any], issue_number: int) -> bool:
    """Check if an open PR references issue_number via title, branch or body."""
    issue_str = str(issue_number)
    title = str(pr.get("title") or "")
    body = str(pr.get("body") or "")
    head_ref = str((pr.get("head") or {}).get("ref") or "")

    # 1. Branch name pattern: issue-<N>, <N>-, fix/<N>-, feat/<N>-, <N>_
    branch_pattern = re.compile(rf"(?:^|/|-|_){issue_str}(?:-|_|\b)", re.IGNORECASE)
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
) -> None:
    """Refuse dispatch if an active staff run or open PR already targets (repo, issue)."""
    # 1. Active staff runs in local runner store
    try:
        runner = get_runner()
        if runner and runner.store:
            for r in runner.store.active_runs():
                if r.id == current_run_id:
                    continue
                if r.repo == repo and r.target_kind == "issue" and r.target_ref == str(issue):
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

    if not check_open_pr:  # an audited claim override covers the open-PR refusal (#2562)
        return

    # 2. Open PRs referencing this issue
    from dispatch_premise import _normalize_repo

    full_repo = _normalize_repo(repo)
    endpoint = f"/repos/{full_repo}/pulls?state=open&per_page=100"
    pulls: list[dict[str, Any]] = []
    if gh_get_fn:
        res = await gh_get_fn(endpoint)
        pulls = res if isinstance(res, list) else []
    else:
        try:
            from gh_utils import gh_api  # noqa: PLC0415

            res = await gh_api(endpoint)
            pulls = res if isinstance(res, list) else []
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to query open PRs for %s: %s", full_repo, exc)
            pulls = []

    for pr in pulls:
        if is_issue_referenced_in_pr(pr, issue):
            pr_num = pr.get("number")
            pr_url = pr.get("html_url") or pr.get("url") or f"https://github.com/{full_repo}/pull/{pr_num}"
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "open_pr_exists",
                    "message": (
                        f"Open PR #{pr_num} ({pr_url}) already targets {repo}#{issue}; "
                        f"dispatch 'pr-remediator' with pr={pr_num} instead (or pass force_duplicate=true)."
                    ),
                    "pr": pr_num,
                    "url": pr_url,
                },
            )
