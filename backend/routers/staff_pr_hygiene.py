"""Non-LLM PR hygiene batch endpoints (#3311, part of Grok Bot offload epic #3295).

Deterministic batch runs over explicit ``(repo, pr)`` targets, never cancelling,
admin-merging, force-pushing or closing anything:

- ``POST /pr-hygiene`` lists a target's own head-SHA workflow runs and plans a
  rerun of the ones that failed.
- ``POST /pr-hygiene/closes-refs`` retexts a ``Closes #N`` in a PR body to
  ``Refs #N`` when ``N`` is an epic tracker, so merging the PR cannot wrongly
  auto-close it.
- ``POST /pr-hygiene/rearm`` re-arms auto-merge on a PR that is open, not
  already armed and not held back, via
  ``scripts.automerge_guard.arm_auto_merge`` -- the fleet's one sanctioned
  arming seam (see that module's own docstring: "every fleet code path that
  arms auto-merge must go through it").

All three default to a dry run that only reports the plan. ``update-branch``
remains out of scope: AGENTS.md's Merge Queue section ("Never update PR
branches to keep up with main ... Rebase only for a real conflict"), synced
fleet-wide after this issue was filed, rules out "update a PR's branch just to
keep it fresh" as a batch hygiene action.
"""

# ruff: noqa: B008
from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import Any

import gh_client
from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, require_scope
from pydantic import BaseModel, Field
from staff.audit import record_audit
from staff.maintenance_github import repo_full
from staff.maintenance_policy import MaintenancePreconditionError

# Production (start-dashboard.sh) and the E2E harness (start_e2e_backend.py) both
# launch server.py with only backend/ on sys.path; the repo-root scripts package
# needs this explicit shim (#3311 follow-up).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import automerge_guard  # noqa: E402

#: Strategy passed to the automerge_guard seam. The issue's own text asks for
#: "plain --auto for RD/RM, --auto --squash elsewhere", but arm_auto_merge has
#: no "no strategy" mode -- every call names one explicitly -- and squash is
#: the only strategy this fleet's own AGENTS.md documents for any repo
#: (Quick Reference's own Runner_Dashboard example). Applied uniformly here;
#: flagged to Barb as an assumption rather than guessed silently per-repo.
_REARM_STRATEGY = "squash"

log = logging.getLogger("dashboard.staff.pr_hygiene")

router = APIRouter(tags=["staff-pr-hygiene"])

MAX_ITEMS = 20
_STAFF_ADMIN = require_scope("staff.admin")

#: GitHub's "close/closes/closed" keyword only (this repo's documented PR-body
#: convention, per AGENTS.md); "fix(es/ed)"/"resolve(s/d)" are out of scope.
_CLOSES_RE = re.compile(r"\b(?:closes?|closed)\s+#(\d+)\b", re.IGNORECASE)

#: Labels marking an issue as a multi-PR tracker that no single PR should close.
_EPIC_LABELS = frozenset({"epic", "type:epic"})


class PrHygieneItem(BaseModel):
    repo: str = Field(min_length=1, max_length=200)
    pr: int = Field(gt=0)


class PrHygieneRequest(BaseModel):
    items: list[PrHygieneItem] = Field(min_length=1, max_length=MAX_ITEMS)
    dry_run: bool = True


async def _failed_runs(full_repo: str, head_sha: str) -> list[dict[str, Any]]:
    """Return ``{run_id, name, attempt}`` for this head SHA's own completed, failed runs."""
    page = await gh_client.get(f"/repos/{full_repo}/actions/runs?head_sha={head_sha}&per_page=100")
    runs = page.get("workflow_runs", []) if isinstance(page, dict) else []
    return [
        {"run_id": run["id"], "name": run.get("name") or "", "attempt": run.get("run_attempt") or 1}
        for run in runs
        if run.get("status") == "completed" and run.get("conclusion") == "failure"
    ]


async def _plan_one(item: PrHygieneItem) -> dict[str, Any]:
    """Pre: ``item.repo`` is in the org. Post: a plan naming every rerunnable failed run."""
    full = repo_full(item.repo)
    pr = await gh_client.get(f"/repos/{full}/pulls/{item.pr}")
    head = pr.get("head") if isinstance(pr, dict) else None
    if not head or not head.get("sha"):
        raise HTTPException(status_code=404, detail=f"{item.repo}#{item.pr} not found")
    failed = await _failed_runs(full, head["sha"])
    return {
        "repo": item.repo,
        "pr": item.pr,
        "state": pr.get("state"),
        "failed_runs": failed,
        "action": "rerun_failed_jobs" if failed else "none",
    }


async def _apply_one(plan: dict[str, Any]) -> dict[str, Any]:
    """Rerun every failed run named in ``plan``; a run's own failure never aborts the batch."""
    full = repo_full(plan["repo"])
    applied: list[dict[str, Any]] = []
    for run in plan["failed_runs"]:
        try:
            await gh_client.rerun_failed(full, run["run_id"])
            applied.append({"run_id": run["run_id"], "rerun_requested": True})
        except gh_client.GhClientError as exc:
            applied.append({"run_id": run["run_id"], "rerun_requested": False, "error": str(exc)})
    success = all(a["rerun_requested"] for a in applied)
    return {**plan, "applied": applied, "success": success}


async def _collect_plans(items: list[PrHygieneItem], plan_one: Any) -> list[dict[str, Any]]:
    """Run ``plan_one`` over every item, mapping its errors onto the shared HTTP contract."""
    try:
        return [await plan_one(item) for item in items]
    except MaintenancePreconditionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except gh_client.GhNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except gh_client.GhAuthError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub rejected the token: {exc}") from exc
    except gh_client.GhClientError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub refused the request: {exc}") from exc


@router.post("/pr-hygiene")
async def pr_hygiene(
    body: PrHygieneRequest,
    caller: Principal = Depends(_STAFF_ADMIN),
) -> dict[str, Any]:
    """Batch, deterministic PR hygiene: rerun each target's own failed workflow runs.

    Pre: every item names an in-org repo and an existing PR. Post: one result per
    item; a dry run changes nothing on GitHub, a live run reruns only the failed
    runs it planned to rerun, and the batch is always audited.
    """
    plans = await _collect_plans(body.items, _plan_one)

    if body.dry_run:
        results = [{**plan, "dry_run": True} for plan in plans]
        outcome = "success"
    else:
        results = [{**(await _apply_one(plan)), "dry_run": False} for plan in plans]
        outcome = "success" if all(r["success"] for r in results) else "failure"

    record_audit(
        action="maintenance",
        target="pr-hygiene",
        principal=caller.id,
        surface="api",
        outcome=outcome,
        detail={"dry_run": body.dry_run, "items": results},
    )
    return {"dry_run": body.dry_run, "results": results}


async def _epic_closes_in_body(full: str, body: str) -> list[dict[str, Any]]:
    """Return one ``{issue, labels}`` entry per distinct ``Closes #N`` whose issue is an epic."""
    flagged: list[dict[str, Any]] = []
    seen: set[int] = set()
    for match in _CLOSES_RE.finditer(body):
        number = int(match.group(1))
        if number in seen:
            continue
        seen.add(number)
        issue = await gh_client.get(f"/repos/{full}/issues/{number}")
        labels = {lbl.get("name", "") for lbl in issue.get("labels", [])} if isinstance(issue, dict) else set()
        if labels & _EPIC_LABELS:
            flagged.append({"issue": number, "labels": sorted(labels)})
    return flagged


def _retext_closes_to_refs(body: str, issue_numbers: set[int]) -> str:
    """Rewrite only the ``Closes #N`` occurrences naming one of ``issue_numbers`` to ``Refs #N``."""

    def _sub(match: re.Match[str]) -> str:
        number = int(match.group(1))
        return f"Refs #{number}" if number in issue_numbers else match.group(0)

    return _CLOSES_RE.sub(_sub, body)


async def _plan_one_closes_refs(item: PrHygieneItem) -> dict[str, Any]:
    """Pre: ``item.repo`` is in the org. Post: a plan naming every epic-targeting ``Closes #N``."""
    full = repo_full(item.repo)
    pr = await gh_client.get(f"/repos/{full}/pulls/{item.pr}")
    body = pr.get("body") or "" if isinstance(pr, dict) else ""
    flagged = await _epic_closes_in_body(full, body)
    return {
        "repo": item.repo,
        "pr": item.pr,
        "flagged": flagged,
        "action": "fix_closes_refs" if flagged else "none",
        "_body": body,
    }


async def _apply_one_closes_refs(plan: dict[str, Any]) -> dict[str, Any]:
    """Retext ``plan``'s body for its flagged issues only; a plan with none is a no-op."""
    if not plan["flagged"]:
        return {**plan, "patched": False}
    full = repo_full(plan["repo"])
    numbers = {entry["issue"] for entry in plan["flagged"]}
    new_body = _retext_closes_to_refs(plan["_body"], numbers)
    await gh_client.patch(f"/repos/{full}/pulls/{plan['pr']}", json={"body": new_body})
    return {**plan, "patched": True}


def _without_body(plan: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in plan.items() if k != "_body"}


@router.post("/pr-hygiene/closes-refs")
async def pr_hygiene_closes_refs(
    body: PrHygieneRequest,
    caller: Principal = Depends(_STAFF_ADMIN),
) -> dict[str, Any]:
    """Batch, deterministic fix for a ``Closes #N`` that wrongly targets an epic tracker.

    Pre: every item names an in-org repo and an existing PR. Post: one result per
    item; a dry run changes nothing on GitHub, a live run retexts only the flagged
    ``Closes #N`` occurrences to ``Refs #N``, and the batch is always audited.
    """
    plans = await _collect_plans(body.items, _plan_one_closes_refs)

    if body.dry_run:
        results = [{**_without_body(plan), "dry_run": True} for plan in plans]
    else:
        results = [{**_without_body(await _apply_one_closes_refs(plan)), "dry_run": False} for plan in plans]

    record_audit(
        action="maintenance",
        target="pr-hygiene-closes-refs",
        principal=caller.id,
        surface="api",
        outcome="success",
        detail={"dry_run": body.dry_run, "items": results},
    )
    return {"dry_run": body.dry_run, "results": results}


async def _plan_one_rearm(item: PrHygieneItem) -> dict[str, Any]:
    """Pre: ``item.repo`` is in the org. Post: a plan naming whether ``item`` needs re-arming."""
    full = repo_full(item.repo)
    pr = await gh_client.get(f"/repos/{full}/pulls/{item.pr}")
    state = pr.get("state") if isinstance(pr, dict) else None
    if state != "open":
        return {"repo": item.repo, "pr": item.pr, "state": state, "action": "none", "reason": "PR is not open"}
    if isinstance(pr, dict) and pr.get("auto_merge"):
        return {
            "repo": item.repo,
            "pr": item.pr,
            "state": state,
            "action": "none",
            "reason": "auto-merge already armed",
        }
    verdict = await asyncio.to_thread(automerge_guard.evaluate_hold, full, item.pr)
    if verdict.held:
        return {"repo": item.repo, "pr": item.pr, "state": state, "action": "none", "reason": verdict.describe()}
    return {"repo": item.repo, "pr": item.pr, "state": state, "action": "rearm"}


async def _apply_one_rearm(plan: dict[str, Any]) -> dict[str, Any]:
    """Re-arm auto-merge for a ``plan`` naming ``action == "rearm"``; any other plan is a no-op."""
    if plan["action"] != "rearm":
        return {**plan, "armed": False}
    full = repo_full(plan["repo"])
    result = await asyncio.to_thread(automerge_guard.arm_auto_merge, full, plan["pr"], strategy=_REARM_STRATEGY)
    armed = result.armed or result.merged
    return {**plan, "armed": armed, "detail": result.detail if armed else result.reason()}


@router.post("/pr-hygiene/rearm")
async def pr_hygiene_rearm(
    body: PrHygieneRequest,
    caller: Principal = Depends(_STAFF_ADMIN),
) -> dict[str, Any]:
    """Batch re-arm of an unarmed, unheld PR via the fleet's sanctioned ``automerge_guard`` seam.

    Pre: every item names an in-org repo and an existing PR. Post: one result per
    item; a dry run changes nothing on GitHub, a live run re-arms only a PR that
    is open, not already armed and not held back (``automerge_guard.evaluate_hold``),
    and the batch is always audited. Never admin-merges, force-pushes or closes.
    """
    plans = await _collect_plans(body.items, _plan_one_rearm)

    if body.dry_run:
        results = [{**plan, "dry_run": True} for plan in plans]
        outcome = "success"
    else:
        results = [{**(await _apply_one_rearm(plan)), "dry_run": False} for plan in plans]
        outcome = "success" if all(r["action"] == "none" or r["armed"] for r in results) else "failure"

    record_audit(
        action="maintenance",
        target="pr-hygiene-rearm",
        principal=caller.id,
        surface="api",
        outcome=outcome,
        detail={"dry_run": body.dry_run, "items": results},
    )
    return {"dry_run": body.dry_run, "results": results}


__all__ = ["router"]
