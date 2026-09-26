"""File an approved plan on GitHub (CR-4, #1285).

Creates the plan epic, then each child in dependency-wave order (so dependency refs
resolve to real issue numbers), posts each child's turnover document as its first
comment and links the child as a sub-issue of the epic. Progress is recorded on the
``FilingProgress`` passed in after every GitHub write, so an interrupted filing can be
retried without creating duplicates.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

from code_requests.model import CodeRequest
from code_requests.plan import PlanChild, PlanDraft, compute_waves
from code_requests.plan_render import TurnoverIdentity, render_child_body, render_epic_body, render_turnover
from code_requests.planner import FilingProgress

Fetch = Callable[[str], Awaitable[Any]]
Write = Callable[..., Awaitable[Any]]

EPIC_LABELS = ["epic", "code-request-plan"]
_WORD = re.compile(r"[A-Za-z0-9]{4,}")


def child_labels(child: PlanChild) -> list[str]:
    return [child.tier, f"complexity:{child.complexity}", child.task_class, "code-request-plan"]


async def find_duplicates(fetch: Fetch, repo_full: str, title: str, *, exclude: set[int]) -> list[str]:
    """Open issues in ``repo_full`` whose titles share keywords with ``title``."""
    words = _WORD.findall(title)[:5]
    if not words:
        return []
    query = quote(f"repo:{repo_full} is:issue is:open in:title {' '.join(words)}")
    result = await fetch(f"/search/issues?q={query}&per_page=10")
    items = result.get("items", []) if isinstance(result, dict) else []
    return [
        f"#{item['number']} {item.get('title', '')} — found by keyword search at filing"
        for item in items
        if isinstance(item, dict) and item.get("number") not in exclude
    ]


def _dependency_refs(child: PlanChild, numbers: dict[str, int]) -> list[str]:
    local = [f"#{numbers[d]}" for d in child.local_dependencies()]
    return local + child.external_dependencies()


async def file_plan(
    request: CodeRequest,
    plan: PlanDraft,
    progress: FilingProgress,
    *,
    org: str,
    fetch: Fetch,
    write: Write,
) -> FilingProgress:
    """File ``plan`` for ``request``; mutates and returns ``progress``.

    Preconditions: the plan passed ``validate_plan`` and an operator approved it (or
    the profile waives approval). Postcondition: the epic exists, every child exists
    with its turnover comment and is linked under the epic.
    """
    assert request.issue_number is not None, "a Code Request is filed only once it has an issue"
    repo_full = f"{org}/{request.repository}"
    base = f"/repos/{repo_full}"
    cr_ref = request.issue_url or f"{repo_full}#{request.issue_number}"

    if progress.epic_number is None:
        cited = {d.number for d in plan.duplicates}
        extra = await find_duplicates(fetch, repo_full, plan.epic.title, exclude=cited | {request.issue_number})
        body = render_epic_body(plan, code_request_ref=cr_ref, extra_duplicates=extra)
        epic = await write(
            f"{base}/issues", method="POST", json_body={"title": plan.epic.title, "body": body, "labels": EPIC_LABELS}
        )
        progress.epic_number, progress.epic_url = int(epic["number"]), str(epic.get("html_url") or "")

    commit = await fetch(f"{base}/commits/{quote(request.branch, safe='')}")
    baseline = str(commit.get("sha", ""))[:12] if isinstance(commit, dict) else ""
    assert baseline, f"could not resolve the baseline commit of {repo_full}@{request.branch}"

    by_key = {c.key: c for c in plan.children}
    epic_ref = f"#{progress.epic_number}"
    for wave in compute_waves(plan.children):
        for key in wave:
            child = by_key[key]
            if key not in progress.children:
                body = render_child_body(
                    child, epic_ref=epic_ref, dependency_refs=_dependency_refs(child, progress.children)
                )
                created = await write(
                    f"{base}/issues",
                    method="POST",
                    json_body={"title": child.title, "body": body, "labels": child_labels(child)},
                )
                progress.children[key], progress.child_ids[key] = int(created["number"]), int(created["id"])
            number = progress.children[key]
            if key not in progress.turnovers_posted:
                identity = TurnoverIdentity(
                    repository=repo_full,
                    base_branch=request.branch,
                    baseline_sha=baseline,
                    governing_issue=f"#{number} (plan epic {epic_ref})",
                )
                await write(
                    f"{base}/issues/{number}/comments",
                    method="POST",
                    json_body={"body": render_turnover(child, identity)},
                )
                progress.turnovers_posted.append(key)
            if key not in progress.linked:
                await write(
                    f"{base}/issues/{progress.epic_number}/sub_issues",
                    method="POST",
                    json_body={"sub_issue_id": progress.child_ids[key]},
                )
                progress.linked.append(key)
    return progress
