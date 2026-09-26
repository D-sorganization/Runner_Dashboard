"""Work request action executors and verifiers (SC-G5-1 slice B, #1497).

Executors for:
- ``ci.remediate``
- ``issue.act``
- ``pr.act``
- ``code_request.dispatch``
- ``assessment.run``

Executors run synchronously in an anyio worker thread; coroutines are sent to the
event loop with :func:`staff.loop_bridge.run_on_loop`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from staff.loop_bridge import BridgeUnavailableError, run_on_loop
from staff.work_request_dispatch import (
    _dispatch_assessment_workflow,
    _dispatch_code_request_workflow,
    _dispatch_issue_action,
    _dispatch_issue_or_pr_action,
    _dispatch_pr_action,
    _dispatch_remediation_workflow,
    _dispatch_workflow_json,
)

if TYPE_CHECKING:
    from staff.actions import ActionContext, ActionRegistry, ActionResult

__all__ = [
    "_dispatch_assessment_workflow",
    "_dispatch_code_request_workflow",
    "_dispatch_issue_action",
    "_dispatch_issue_or_pr_action",
    "_dispatch_pr_action",
    "_dispatch_remediation_workflow",
    "_dispatch_workflow_json",
    "execute_assessment_run",
    "execute_ci_remediate",
    "execute_code_request_dispatch",
    "execute_issue_act",
    "execute_pr_act",
    "register_work_request_actions",
]

log = logging.getLogger("dashboard.staff.work_request_executors")

# ─── Action Executors ────────────────────────────────────────────────────────


def execute_ci_remediate(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    repo = str(params.get("repo") or "").strip()
    run_id = params.get("run_id")
    provider = params.get("provider")
    prompt = str(params.get("prompt") or "")
    machine = str(params.get("machine") or "local")

    if ctx.dry_run:
        plan = {
            "action": "ci.remediate",
            "repo": repo,
            "run_id": run_id,
            "provider": provider or "claude",
            "prompt": prompt,
            "machine": machine,
            "workflow": "Agent-CI-Remediation.yml",
        }
        return ActionResult(success=True, result=plan)

    try:
        res = run_on_loop(
            _dispatch_remediation_workflow,
            repo,
            int(run_id) if run_id is not None else None,
            str(provider) if provider else None,
            prompt,
            machine,
            "main",
        )
        return ActionResult(success=True, result=res)
    except BridgeUnavailableError as exc:
        return ActionResult(success=False, error=f"ci.remediate {exc}", failure_class="bridge_unavailable")
    except Exception as exc:  # noqa: BLE001
        return ActionResult(success=False, error=str(exc), failure_class="dispatch_failed")


def _run_bulk_dispatches(
    dispatch_fn: Any,
    repo: str,
    target_key: str,
    targets: list[int],
    provider: str | None,
    model: str | None,
    prompt: str,
    role: str | None,
    machine: str,
) -> ActionResult:
    from staff.actions import ActionResult

    if not targets:
        return ActionResult(success=False, error=f"no {target_key} target specified", failure_class="dispatch_failed")

    if len(targets) == 1:
        try:
            res = run_on_loop(
                dispatch_fn,
                repo,
                targets[0],
                str(provider) if provider else None,
                str(model) if model else None,
                prompt,
                str(role) if role else None,
                machine,
            )
            return ActionResult(success=True, result=res)
        except BridgeUnavailableError as exc:
            return ActionResult(success=False, error=f"{target_key}.act {exc}", failure_class="bridge_unavailable")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(success=False, error=str(exc), failure_class="dispatch_failed")

    dispatched: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for num in targets:
        try:
            item_res = run_on_loop(
                dispatch_fn,
                repo,
                num,
                str(provider) if provider else None,
                str(model) if model else None,
                prompt,
                str(role) if role else None,
                machine,
            )
            dispatched.append(item_res)
        except Exception as exc:  # noqa: BLE001
            rejected.append({"number": num, "error": str(exc)})

    bulk_result = {
        "status": "partial" if rejected and dispatched else ("failed" if not dispatched else "dispatched"),
        "accepted": len(dispatched),
        "dispatched": dispatched,
        "rejected": rejected,
    }
    if not dispatched:
        return ActionResult(
            success=False,
            error=f"{target_key}.act failed for every target: "
            + "; ".join(f"#{r['number']}: {r['error']}" for r in rejected),
            failure_class="dispatch_failed",
            result=bulk_result,
        )
    return ActionResult(success=True, result=bulk_result)


def execute_issue_act(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    repo = str(params.get("repo") or "").strip()
    issue = params.get("issue")
    issues = [int(i) for i in (params.get("issues") or []) if i is not None]
    if not issues and issue is not None:
        issues = [int(issue)]
    provider = params.get("provider")
    model = params.get("model")
    prompt = str(params.get("prompt") or "")
    role = params.get("role")
    machine = str(params.get("machine") or "local")
    force = bool(params.get("force", False))
    approved_by = str(params.get("approved_by") or "")

    if ctx.dry_run:
        plan = {
            "action": "issue.act",
            "repo": repo,
            "issue": issues[0] if len(issues) == 1 else None,
            "issues": issues,
            "provider": provider or "claude",
            "model": model,
            "prompt": prompt,
            "role": role,
            "machine": machine,
            "force": force,
            "approved_by": approved_by,
            "workflow": "Agent-Issue-Dispatch.yml",
        }
        return ActionResult(success=True, result=plan)

    return _run_bulk_dispatches(_dispatch_issue_action, repo, "issue", issues, provider, model, prompt, role, machine)


def execute_pr_act(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    repo = str(params.get("repo") or "").strip()
    pr = params.get("pr")
    prs = [int(p) for p in (params.get("prs") or []) if p is not None]
    if not prs and pr is not None:
        prs = [int(pr)]
    provider = params.get("provider")
    model = params.get("model")
    prompt = str(params.get("prompt") or "")
    role = params.get("role")
    machine = str(params.get("machine") or "local")
    force = bool(params.get("force", False))
    approved_by = str(params.get("approved_by") or "")

    if ctx.dry_run:
        plan = {
            "action": "pr.act",
            "repo": repo,
            "pr": prs[0] if len(prs) == 1 else None,
            "prs": prs,
            "provider": provider or "claude",
            "model": model,
            "prompt": prompt,
            "role": role,
            "machine": machine,
            "force": force,
            "approved_by": approved_by,
            "workflow": "Agent-PR-Dispatch.yml",
        }
        return ActionResult(success=True, result=plan)

    return _run_bulk_dispatches(_dispatch_pr_action, repo, "pr", prs, provider, model, prompt, role, machine)


def execute_code_request_dispatch(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    repo = str(params.get("repo") or "").strip()
    ref = str(params.get("ref") or "main")
    provider = params.get("provider")
    model = params.get("model")
    profile_id = params.get("profile_id")
    prompt = str(params.get("prompt") or "")

    if ctx.dry_run:
        plan = {
            "action": "code_request.dispatch",
            "repo": repo,
            "ref": ref,
            "provider": provider or "claude",
            "model": model,
            "profile_id": profile_id,
            "prompt": prompt,
        }
        return ActionResult(success=True, result=plan)

    try:
        res = run_on_loop(
            _dispatch_code_request_workflow,
            repo,
            ref,
            str(provider) if provider else None,
            str(model) if model else None,
            str(profile_id) if profile_id else None,
            prompt,
        )
        return ActionResult(success=True, result=res)
    except BridgeUnavailableError as exc:
        return ActionResult(success=False, error=f"code_request.dispatch {exc}", failure_class="bridge_unavailable")
    except Exception as exc:  # noqa: BLE001
        return ActionResult(success=False, error=str(exc), failure_class="dispatch_failed")


def execute_assessment_run(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    repo = str(params.get("repo") or "").strip()
    provider = params.get("provider")
    prompt = str(params.get("prompt") or "")

    if ctx.dry_run:
        plan = {
            "action": "assessment.run",
            "repo": repo,
            "provider": provider or "jules_api",
            "prompt": prompt,
            "workflow": "Jules-Assess-Repo.yml",
        }
        return ActionResult(success=True, result=plan)

    try:
        res = run_on_loop(
            _dispatch_assessment_workflow,
            repo,
            str(provider) if provider else None,
            prompt,
        )
        return ActionResult(success=True, result=res)
    except BridgeUnavailableError as exc:
        return ActionResult(success=False, error=f"assessment.run {exc}", failure_class="bridge_unavailable")
    except Exception as exc:  # noqa: BLE001
        return ActionResult(success=False, error=str(exc), failure_class="dispatch_failed")


# ─── Action Registration ─────────────────────────────────────────────────────


_ACT_SCHEMA = {
    "repo": "string",
    "provider": "string?",
    "model": "string?",
    "prompt": "string?",
    "role": "string?",
    "machine": "string?",
    "force": "bool?",
    "approved_by": "string?",
}


def register_work_request_actions(registry: ActionRegistry) -> None:
    """Register work-request actions with ACTION_REGISTRY."""
    from staff.actions import ActionDefinition, ActionRiskClass

    defs = [
        ActionDefinition(
            name="ci.remediate",
            description="Dispatch a CI remediation agent for a failed workflow run.",
            params_schema={
                "repo": "string",
                "run_id": "int?",
                "provider": "string?",
                "prompt": "string?",
                "machine": "string?",
            },
            required_scope="remediation.dispatch",
            risk_class=ActionRiskClass.MEDIUM,
            executor=execute_ci_remediate,
        ),
        ActionDefinition(
            name="issue.act",
            description="Dispatch an agent to analyze or remediate an issue.",
            params_schema={**_ACT_SCHEMA, "issue": "int?", "issues": "list[int]?"},
            required_scope="workflows.dispatch",
            risk_class=ActionRiskClass.MEDIUM,
            executor=execute_issue_act,
        ),
        ActionDefinition(
            name="pr.act",
            description="Dispatch an agent to review or act on a pull request.",
            params_schema={**_ACT_SCHEMA, "pr": "int?", "prs": "list[int]?"},
            required_scope="workflows.dispatch",
            risk_class=ActionRiskClass.MEDIUM,
            executor=execute_pr_act,
        ),
        ActionDefinition(
            name="code_request.dispatch",
            description="Dispatch a Code Request implementation workflow.",
            params_schema={
                "repo": "string",
                "ref": "string?",
                "provider": "string?",
                "model": "string?",
                "profile_id": "string?",
                "prompt": "string?",
            },
            required_scope="code_requests.write",
            risk_class=ActionRiskClass.MEDIUM,
            executor=execute_code_request_dispatch,
        ),
        ActionDefinition(
            name="assessment.run",
            description="Run an assessment workflow for a repository.",
            params_schema={"repo": "string", "provider": "string?", "prompt": "string?"},
            required_scope="assessments.dispatch",
            risk_class=ActionRiskClass.LOW,
            executor=execute_assessment_run,
        ),
    ]
    for d in defs:
        registry.register(d)
