"""RD-1 CI-fix launch path (#1881, #1879).

One core, :func:`dispatch_ci_fix`, serves both the operator ``POST /dispatch`` route and the
GitHub webhook. It takes the per-PR lock, routes, builds a bounded prompt, launches through
the staff dispatch path (``staff.dispatch_service.dispatch_staff_run`` with the ``ad-hoc``
role on this node), binds the lock to the launched run and writes the audit row.

:func:`handle_ci_fix_event` turns a parsed webhook event into a dispatch request: it reads
the PR (draft / auto-merge / state) and the failing job's log over REST, applies the
existing eligibility rules and never raises, so a CI-fix failure cannot affect other routes.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import gh_client
from ci_fix_dispatch import (
    GLOBAL_CI_FIX_LOCK_MGR,
    build_ci_fix_prompt,
    classify_failure_type,
    count_consecutive_attempts,
    evaluate_ci_fix_trigger,
    extract_failing_test_names,
    failure_signature,
    pr_number_from_queue_ref,
    record_ci_fix_audit,
    route_ci_fix,
    truncate_log_tail,
)
from ci_fix_events import FAILED_CONCLUSIONS, CIFixEvent
from dashboard_config import ORG
from fastapi import HTTPException
from identity import Principal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from staff.adapters import available_providers
from staff.dispatch_service import DispatchCommand, dispatch_staff_run

log = logging.getLogger("dashboard.ci_fix_service")

# Route provider ids (``agent_remediation`` naming) → staff adapter ids.
ROUTE_TO_STAFF_PROVIDER = {"claude_code_cli": "claude", "codex_cli": "codex", "antigravity": "antigravity"}
CI_FIX_ROLE = "ad-hoc"
WEBHOOK_PRINCIPAL = Principal(
    id="ci-fix-webhook",
    type="bot",
    name="RD-1 CI-fix (GitHub webhook)",
    scopes=["remediation.dispatch"],
)
MERGE_QUEUE_WORKFLOW = "merge-queue"

Launcher = Callable[[DispatchCommand, Principal], Awaitable[dict[str, Any]]]
FailureType = Literal["lint", "test", "security", "spec", "conflict", "unknown"]


class CIFixBusyError(Exception):
    """A CI-fix session already holds this PR's lock."""


class CIFixLaunchUnavailableError(Exception):
    """The routed provider is not installed on this node; nothing was launched."""


class CIFixDispatchRequest(BaseModel):
    """Flat, validated input of one CI-fix dispatch (DbC for ``POST /dispatch`` and the webhook)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, populate_by_name=True)

    repo: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$", validation_alias=AliasChoices("repo", "repository")
    )
    pr_number: int = Field(gt=0, validation_alias=AliasChoices("pr_number", "number"))
    kind: Literal["ci_failure", "merge_conflict"] = "ci_failure"
    # The PR head branch the session checks out and pushes to (#1887 review): required, same repo.
    branch: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._/-]+$")
    base_ref: str = Field(default="main", max_length=255)
    workflow_name: str = Field(default="", max_length=200)
    run_id: int | None = None
    log_tail: str = ""
    pr_diff: str = ""
    attempt_number: int | None = Field(default=None, ge=1)
    failure_type: FailureType | None = None
    failing_tests: list[str] | None = None
    conflicting_files: list[str] | None = None
    session_id: str = Field(default="", max_length=200)
    from_queue: bool = False


def _pr_head_note(branch: str) -> str:
    """Pin the session to the PR head; stored in the prompt, so a retried run keeps it."""
    return "\n".join(
        [
            f"PR head: this worktree starts at `origin/{branch}`. If HEAD is not based on it, run",
            f"`git fetch origin {branch} && git reset --hard origin/{branch}` before editing.",
            f"Push with `git push origin HEAD:{branch}` (never force). Do not open a new pull request:",
            "this fixes the existing one, which overrides the fleet rule to open a PR.",
            "",
        ]
    )


def build_merge_conflict_prompt(repo: str, pr_number: int, branch: str, base_ref: str) -> str:
    """Prompt for a PR the merge queue removed with ``MERGE_CONFLICT`` (#1879)."""
    full = f"{ORG}/{repo}"
    return "\n".join(
        [
            f"Merge-Conflict Task: Repository {full} PR #{pr_number} (Branch: {branch or 'see PR'})",
            "",
            f"The merge queue removed this PR because it conflicts with `{base_ref}`.",
            "",
            "Instructions:",
            f"1. Merge `origin/{base_ref}` into the PR head with a merge commit (do not rebase or force-push).",
            "2. Resolve conflicts. In SPEC.md change-log tables, docs/development/HANDOFF.md and",
            "   docs/development/DEVELOPMENT_LOG.md keep both rows/entries (they are keyed by PR or issue;",
            "   never renumber). Prefer change fragments where the repository has them.",
            "3. Run `python -m scripts.pre_pr` (or the repository's fast checks) and fix only merge fallout.",
            "4. Push once, then re-arm squash auto-merge through Repository_Management's",
            f"   `scripts/automerge_guard.py {full} {pr_number} --arm --strategy squash`.",
            "5. End the session.",
        ]
    )


def _queue_note(repo: str, pr_number: int) -> str:
    return "\n".join(
        [
            "Merge queue: this failure happened in the merge queue, which removed the PR. After pushing,",
            "re-arm squash auto-merge through Repository_Management's",
            f"`scripts/automerge_guard.py {ORG}/{repo} {pr_number} --arm --strategy squash`.",
        ]
    )


async def dispatch_ci_fix(
    req: CIFixDispatchRequest,
    caller: Principal,
    *,
    launcher: Launcher | None = None,
) -> dict[str, Any]:
    """Launch one capped CI-fix session for a PR.

    Pre: ``req`` is validated; ``caller`` is the authenticated principal (rate limit, audit).
    Post: on success exactly one staff run was admitted, the PR lock is bound to it, and one
    audit row names it. On any refusal the lock is released and nothing is audited.
    Raises :class:`CIFixBusyError`, :class:`CIFixLaunchUnavailableError`, or the staff path's
    ``HTTPException``.
    """
    locks = GLOBAL_CI_FIX_LOCK_MGR
    session_id = req.session_id or f"ci-fix-{req.repo}-{req.pr_number}"
    if not locks.acquire(req.repo, req.pr_number, session_id=session_id):
        raise CIFixBusyError(f"A CI-fix session is already active for {req.repo}#{req.pr_number}")
    try:
        result = await _route_and_launch(req, caller, session_id, launcher or dispatch_staff_run)
    except BaseException:
        locks.release(req.repo, req.pr_number)
        raise
    if result["staff_run_id"]:
        locks.attach_run(req.repo, req.pr_number, result["staff_run_id"])
    return result


async def _route_and_launch(
    req: CIFixDispatchRequest, caller: Principal, session_id: str, launcher: Launcher
) -> dict[str, Any]:
    is_conflict = req.kind == "merge_conflict"
    log_tail = truncate_log_tail(req.log_tail, max_lines=200)
    failure_type = (
        "conflict" if is_conflict else (req.failure_type or classify_failure_type(req.workflow_name, log_tail))
    )
    failing_tests = req.failing_tests if req.failing_tests is not None else extract_failing_test_names(log_tail)
    workflow_name = req.workflow_name or (MERGE_QUEUE_WORKFLOW if is_conflict else "")
    signature = failure_signature(workflow_name, failing_tests, failure_type)
    attempt = req.attempt_number or count_consecutive_attempts(req.repo, req.pr_number, signature) + 1
    route = route_ci_fix(failure_type, attempt_number=attempt)
    if is_conflict:
        prompt = build_merge_conflict_prompt(req.repo, req.pr_number, req.branch, req.base_ref)
    else:
        prompt = build_ci_fix_prompt(
            repo=req.repo,
            pr_number=req.pr_number,
            branch=req.branch,
            log_tail=log_tail,
            failing_tests=failing_tests,
            pr_diff=req.pr_diff,
            conflicting_files=req.conflicting_files,
        )
        if req.from_queue:
            prompt = f"{prompt}\n{_queue_note(req.repo, req.pr_number)}\n"
    prompt = f"{_pr_head_note(req.branch)}\n{prompt}"
    provider = ROUTE_TO_STAFF_PROVIDER[route.provider]
    if not available_providers().get(provider, False):
        raise CIFixLaunchUnavailableError(
            f"CI-fix route {route.tier}/{route.model} needs provider '{provider}', which is not installed on this "
            "node; install it or dispatch from a node that has it"
        )
    cmd = DispatchCommand(
        role=CI_FIX_ROLE,
        requested_by=f"ci-fix:{caller.id}",
        provider=provider,
        model=route.model,
        repo=req.repo,
        pr=req.pr_number,
        prompt=prompt,
        machine="local",
        surface="ci_fix",
        skip_premise_check=True,
        head_ref=req.branch,
    )
    launched = await launcher(cmd, caller)
    staff_run_id = str((launched.get("run") or {}).get("id") or "")
    record_ci_fix_audit(
        repo=req.repo,
        pr_number=req.pr_number,
        workflow_name=workflow_name,
        run_id=req.run_id,
        failure_type=failure_type,
        provider=route.provider,
        model=route.model,
        attempt_number=attempt,
        cost_budget=route.cost_budget,
        staff_run_id=staff_run_id,
        effort=route.effort,
        failure_signature=signature,
    )
    log.info(
        "ci-fix: dispatched %s#%d attempt=%d route=%s run=%s",
        req.repo,
        req.pr_number,
        attempt,
        route.tier,
        staff_run_id,
    )
    return {
        "status": "dispatched",
        "repo": req.repo,
        "pr_number": req.pr_number,
        "route": route.to_dict(),
        "prompt": prompt,
        "session_id": session_id,
        "failing_tests": failing_tests,
        "attempt_number": attempt,
        "staff_run_id": staff_run_id,
    }


# ─── webhook → dispatch ──────────────────────────────────────────────────────


async def fetch_run_log_tail(full_repo: str, run_id: int) -> str:
    """Last ≤200 lines of the first failed job of a run; ``""`` when unavailable (best-effort)."""
    try:
        jobs = await gh_client.get(f"/repos/{full_repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=50")
        failed = [j for j in jobs.get("jobs", []) if str(j.get("conclusion") or "") in FAILED_CONCLUSIONS]
        if not failed:
            return ""
        text = await gh_client.get_text(f"/repos/{full_repo}/actions/jobs/{failed[0]['id']}/logs")
    except Exception as exc:  # noqa: BLE001 - a missing log never blocks the dispatch
        log.warning("ci-fix: no log tail for %s run %s: %s", full_repo, run_id, exc)
        return ""
    return truncate_log_tail(f"[job: {failed[0].get('name', '')}]\n{text}", max_lines=200)


async def _find_queue_run(event: CIFixEvent) -> dict[str, Any] | None:
    """The newest failed ``merge_group`` run for this PR (a dequeue carries no run id)."""
    try:
        data = await gh_client.get(
            f"/repos/{event.full_repo}/actions/runs?event=merge_group&status=failure&per_page=20"
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("ci-fix: cannot list merge-group runs for %s: %s", event.full_repo, exc)
        return None
    for run in data.get("workflow_runs", []):
        if pr_number_from_queue_ref(str(run.get("head_branch") or "")) == event.pr_number:
            return dict(run)
    return None


async def _ci_failure_request(event: CIFixEvent) -> CIFixDispatchRequest | str:
    """Request for a failing run (PR or merge-queue), or the reason it is not eligible."""
    run_id, workflow_name = event.run_id, event.workflow_name
    if event.kind == "queue_checks_failed":
        run = await _find_queue_run(event)
        if run is not None:
            run_id, workflow_name = int(run["id"]), str(run.get("name") or "")
    pr: dict[str, Any] = {}
    try:
        pr = await gh_client.get(f"/repos/{event.full_repo}/pulls/{event.pr_number}")
    except Exception as exc:  # noqa: BLE001
        if not event.from_queue:
            return f"cannot read PR {event.full_repo}#{event.pr_number} to rule out a draft: {exc}"
    if pr and str(pr.get("state") or "open") != "open":
        return f"PR {event.full_repo}#{event.pr_number} is not open"
    head_obj = pr.get("head")
    head: dict[str, Any] = head_obj if isinstance(head_obj, dict) else {}
    head_repo = str((head.get("repo") or {}).get("full_name") or event.head_repo)
    if head_repo and head_repo.lower() != event.full_repo.lower():
        return f"PR {event.full_repo}#{event.pr_number} comes from fork {head_repo}; its head cannot be pushed to"
    # A merge-group run's head is the temporary queue ref, never the PR branch.
    fallback = "" if pr_number_from_queue_ref(event.head_branch) else event.head_branch
    branch = str(head.get("ref") or fallback)
    if not branch:
        return f"cannot resolve the head branch of PR {event.full_repo}#{event.pr_number}"
    # A queued PR has merge intent by construction; elsewhere auto-merge must be armed.
    facts = {
        "number": event.pr_number,
        "is_draft": bool(pr.get("draft")) if pr else bool(event.is_draft),
        "auto_merge_armed": event.from_queue or bool(pr.get("auto_merge")),
    }
    decision = evaluate_ci_fix_trigger(
        {
            "conclusion": "failure",
            "workflow_name": workflow_name,
            "repository": event.repo,
            "run_id": run_id,
            "head_branch": event.head_branch,
            "pull_requests": [facts],
        },
        lock_manager=GLOBAL_CI_FIX_LOCK_MGR,
    )
    if not decision.eligible:
        return decision.reason
    return CIFixDispatchRequest(
        repo=event.repo,
        pr_number=event.pr_number,
        branch=branch,
        base_ref=event.base_ref or "main",
        workflow_name=workflow_name,
        run_id=run_id,
        log_tail=await fetch_run_log_tail(event.full_repo, run_id) if run_id else "",
        from_queue=event.from_queue,
    )


def _merge_conflict_request(event: CIFixEvent) -> CIFixDispatchRequest | str:
    if event.is_draft:
        return "Auto-fix is disabled on draft PRs (RD-0)"
    if event.pr_state and event.pr_state != "open":
        return f"PR {event.full_repo}#{event.pr_number} is not open"
    if event.head_repo and event.head_repo.lower() != event.full_repo.lower():
        return f"PR {event.full_repo}#{event.pr_number} comes from fork {event.head_repo}; its head cannot be pushed to"
    if not event.head_branch:
        return f"cannot resolve the head branch of PR {event.full_repo}#{event.pr_number}"
    return CIFixDispatchRequest(
        repo=event.repo,
        pr_number=event.pr_number,
        kind="merge_conflict",
        branch=event.head_branch,
        base_ref=event.base_ref or "main",
        from_queue=True,
    )


async def handle_ci_fix_event(event: CIFixEvent, *, launcher: Launcher | None = None) -> dict[str, Any]:
    """Dispatch at most one CI fix for a webhook event. Never raises (orthogonality)."""
    target = f"{event.full_repo}#{event.pr_number}"
    try:
        if event.kind == "queue_checks_failed" and event.is_draft:
            req: CIFixDispatchRequest | str = "Auto-fix is disabled on draft PRs (RD-0)"
        elif event.kind == "merge_conflict":
            req = _merge_conflict_request(event)
        else:
            req = await _ci_failure_request(event)
        if isinstance(req, str):
            log.info("ci-fix: skipped %s (%s): %s", target, event.kind, req)
            return {"dispatched": False, "reason": req}
        result = await dispatch_ci_fix(req, WEBHOOK_PRINCIPAL, launcher=launcher)
    except CIFixBusyError as exc:
        log.info("ci-fix: skipped %s: %s", target, exc)
        return {"dispatched": False, "reason": str(exc)}
    except HTTPException as exc:
        log.warning("ci-fix: staff dispatch refused %s: %s", target, exc.detail)
        return {"dispatched": False, "reason": f"staff dispatch refused: {exc.detail}"}
    except Exception as exc:  # noqa: BLE001 - includes CIFixLaunchUnavailableError and GitHub outages
        log.warning("ci-fix: event for %s not dispatched: %s", target, exc)
        return {"dispatched": False, "reason": str(exc)}
    return {"dispatched": True, "staff_run_id": result["staff_run_id"], "route": result["route"]}
