"""Non-LLM PR hygiene batch endpoint (#3311, part of Grok Bot offload epic #3295).

Deterministic batch run over explicit ``(repo, pr)`` targets: for each target,
list its own head-SHA workflow runs and plan a rerun of the ones that failed.
Dry run (default) only reports the plan; live execution (``dry_run=False``)
reruns those failed runs and nothing else. This endpoint never cancels,
admin-merges, force-pushes or closes anything -- update-branch, merge-queue
re-arming and PR-body (Closes->Refs) fixes are deliberately out of scope for
this slice and tracked as follow-up work on #3311.
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
from typing import Any

import gh_client
from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, require_scope
from pydantic import BaseModel, Field
from staff.audit import record_audit
from staff.maintenance_github import repo_full
from staff.maintenance_policy import MaintenancePreconditionError

log = logging.getLogger("dashboard.staff.pr_hygiene")

router = APIRouter(tags=["staff-pr-hygiene"])

MAX_ITEMS = 20
_STAFF_ADMIN = require_scope("staff.admin")


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
    try:
        plans = [await _plan_one(item) for item in body.items]
    except MaintenancePreconditionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except gh_client.GhNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except gh_client.GhAuthError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub rejected the token: {exc}") from exc
    except gh_client.GhClientError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub refused the request: {exc}") from exc

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


__all__ = ["router"]
