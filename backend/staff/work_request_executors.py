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

import contextlib
import json
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from dashboard_config import ORG, REPO_ROOT
from staff.loop_bridge import BridgeUnavailableError, run_on_loop
from system_utils import run_cmd

if TYPE_CHECKING:
    from staff.actions import ActionContext, ActionRegistry, ActionResult

log = logging.getLogger("dashboard.staff.work_request_executors")


# ─── Underlying Dispatch Functions ───────────────────────────────────────────


async def _dispatch_workflow_json(endpoint: str, payload: dict[str, Any], prefix: str = "agent-dispatch-") -> None:
    """Helper to write temp JSON payload and execute gh api workflow dispatch."""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=prefix, suffix=".json", delete=False) as f:
        json.dump(payload, f)
        pf = f.name
    try:
        code, _stdout, stderr = await run_cmd(
            ["gh", "api", endpoint, "--method", "POST", "--input", pf],
            timeout=30,
            cwd=REPO_ROOT,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(pf).unlink()
    if code != 0:
        raise RuntimeError(f"Workflow dispatch failed (code {code}): {stderr.strip()[:300]}")


async def _dispatch_remediation_workflow(
    repo: str,
    run_id: int | None,
    provider: str | None,
    prompt: str,
    machine: str = "local",
    ref: str = "main",
) -> dict[str, Any]:
    """Dispatch central CI remediation workflow."""
    from server import _normalize_repository_input

    _, full_repo = _normalize_repository_input(repo)
    chosen_provider = (provider or "claude").strip()
    endpoint = f"/repos/{ORG}/Repository_Management/actions/workflows/Agent-CI-Remediation.yml/dispatches"
    payload = {
        "ref": ref or "main",
        "inputs": {
            "target_repository": full_repo,
            "provider": chosen_provider,
            "run_id": str(run_id or ""),
            "prompt": prompt[:8000],
            "machine": machine,
        },
    }
    await _dispatch_workflow_json(endpoint, payload, prefix="remediation-")
    return {
        "status": "dispatched",
        "workflow": "Agent-CI-Remediation.yml",
        "target_repository": full_repo,
        "provider": chosen_provider,
        "run_id": run_id,
    }


async def _dispatch_issue_or_pr_action(
    kind: str,
    repo: str,
    number: int,
    provider: str | None,
    model: str | None,
    prompt: str,
    role: str | None = None,
    machine: str = "local",
) -> dict[str, Any]:
    """Dispatch workflow action for an issue or pull request."""
    from server import _normalize_repository_input

    _, full_repo = _normalize_repository_input(repo)
    envelope_id = uuid4().hex
    workflow_file = "Agent-Issue-Dispatch.yml" if kind == "issue" else "Agent-PR-Dispatch.yml"
    endpoint = f"/repos/{ORG}/Repository_Management/actions/workflows/{workflow_file}/dispatches"
    inputs = {
        "target_repository": full_repo,
        "number": str(number),
        "provider": (provider or "claude").strip(),
        "model": model or "",
        "prompt": prompt[:8000],
        "role": role or "",
        "envelope_id": envelope_id,
    }
    await _dispatch_workflow_json(endpoint, {"ref": "main", "inputs": inputs}, prefix=f"{kind}-")
    return {
        "status": "dispatched",
        "workflow": workflow_file,
        "target_repository": full_repo,
        kind: number,
        "envelope_id": envelope_id,
    }


async def _dispatch_issue_action(
    repo: str,
    issue: int,
    provider: str | None,
    model: str | None,
    prompt: str,
    role: str | None = None,
    machine: str = "local",
) -> dict[str, Any]:
    return await _dispatch_issue_or_pr_action("issue", repo, issue, provider, model, prompt, role, machine)


async def _dispatch_pr_action(
    repo: str,
    pr: int,
    provider: str | None,
    model: str | None,
    prompt: str,
    role: str | None = None,
    machine: str = "local",
) -> dict[str, Any]:
    return await _dispatch_issue_or_pr_action("pr", repo, pr, provider, model, prompt, role, machine)


async def _dispatch_code_request_workflow(
    repo: str,
    ref: str | None,
    provider: str | None,
    model: str | None,
    profile_id: str | None,
    prompt: str,
) -> dict[str, Any]:
    """Dispatch Code Request implementation workflow."""
    from code_requests.dispatch import trigger_workflow_dispatch

    branch = ref or "main"
    chosen_provider = (provider or "claude").strip()
    code, stderr = await trigger_workflow_dispatch(
        repo=repo,
        branch=branch,
        provider=chosen_provider,
        full_prompt=prompt,
        model=model or "",
        profile_id=profile_id,
    )
    if code != 0:
        raise RuntimeError(f"Code request dispatch failed (code {code}): {stderr.strip()[:300]}")
    return {
        "status": "dispatched",
        "repository": repo,
        "branch": branch,
        "provider": chosen_provider,
        "profile_id": profile_id,
    }


async def _dispatch_assessment_workflow(
    repo: str,
    provider: str | None,
    prompt: str,
) -> dict[str, Any]:
    """Dispatch assessment workflow."""
    chosen_provider = (provider or "jules_api").strip()
    endpoint = f"/repos/{ORG}/Repository_Management/actions/workflows/Jules-Assess-Repo.yml/dispatches"
    payload = {
        "ref": "main",
        "inputs": {"target_repository": f"{ORG}/{repo}", "provider": chosen_provider},
    }
    await _dispatch_workflow_json(endpoint, payload, prefix="assessment-")
    return {
        "status": "dispatched",
        "repository": repo,
        "provider": chosen_provider,
    }


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


def _execute_item_act(kind: str, params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    repo = str(params.get("repo") or "").strip()
    plural = "issues" if kind == "issue" else "prs"
    single = kind
    items_list: list[int] = params.get(plural) or []
    if not items_list and params.get(single) is not None:
        items_list = [int(params[single])]
    provider = params.get("provider")
    model = params.get("model")
    prompt = str(params.get("prompt") or "")
    role = params.get("role")
    machine = str(params.get("machine") or "local")
    workflow = "Agent-Issue-Dispatch.yml" if kind == "issue" else "Agent-PR-Dispatch.yml"

    if ctx.dry_run:
        plan: dict[str, Any] = {
            "action": f"{kind}.act",
            "repo": repo,
            single: items_list[0] if items_list else None,
            "provider": provider or "claude",
            "model": model,
            "prompt": prompt,
            "role": role,
            "machine": machine,
            "workflow": workflow,
        }
        if len(items_list) > 1:
            plan[plural] = items_list
        return ActionResult(success=True, result=plan)

    dispatch_fn = _dispatch_issue_action if kind == "issue" else _dispatch_pr_action
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    targets_map: dict[str, Any] = {}

    for num in items_list:
        try:
            res = run_on_loop(
                dispatch_fn,
                repo,
                int(num),
                str(provider) if provider else None,
                str(model) if model else None,
                prompt,
                str(role) if role else None,
                machine,
            )
            accepted.append(res)
            targets_map[str(num)] = {"status": "dispatched", **res}
        except BridgeUnavailableError as exc:
            rejected.append({single: num, "number": num, "reason": str(exc), "failure_class": "bridge_unavailable"})
            targets_map[str(num)] = {"status": "failed", "error": str(exc), "failure_class": "bridge_unavailable"}
        except Exception as exc:  # noqa: BLE001
            rejected.append({single: num, "number": num, "reason": str(exc), "failure_class": "dispatch_failed"})
            targets_map[str(num)] = {"status": "failed", "error": str(exc), "failure_class": "dispatch_failed"}

    if len(items_list) <= 1:
        if rejected:
            err = rejected[0]["reason"]
            fc = rejected[0].get("failure_class", "dispatch_failed")
            return ActionResult(success=False, error=f"{kind}.act {err}", failure_class=fc)
        return ActionResult(success=True, result=accepted[0] if accepted else {})

    if not accepted:
        err_msg = "; ".join(f"#{r['number']}: {r['reason']}" for r in rejected) or "all dispatches failed"
        fc = rejected[0].get("failure_class", "dispatch_failed") if rejected else "dispatch_failed"
        return ActionResult(
            success=False,
            error=err_msg,
            failure_class=fc,
            result={"accepted": [], "rejected": rejected, "targets": targets_map},
        )

    return ActionResult(
        success=True,
        result={
            "status": "partial" if rejected else "dispatched",
            "accepted": accepted,
            "rejected": rejected,
            "targets": targets_map,
        },
    )


def execute_issue_act(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    return _execute_item_act("issue", params, ctx)


def execute_pr_act(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    return _execute_item_act("pr", params, ctx)


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
            params_schema={
                "repo": "string",
                "issue": "int",
                "provider": "string?",
                "model": "string?",
                "prompt": "string?",
                "role": "string?",
                "machine": "string?",
            },
            required_scope="workflows.dispatch",
            risk_class=ActionRiskClass.MEDIUM,
            executor=execute_issue_act,
        ),
        ActionDefinition(
            name="pr.act",
            description="Dispatch an agent to review or act on a pull request.",
            params_schema={
                "repo": "string",
                "pr": "int",
                "provider": "string?",
                "model": "string?",
                "prompt": "string?",
                "role": "string?",
                "machine": "string?",
            },
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
            params_schema={
                "repo": "string",
                "provider": "string?",
                "prompt": "string?",
            },
            required_scope="assessments.dispatch",
            risk_class=ActionRiskClass.LOW,
            executor=execute_assessment_run,
        ),
    ]
    for d in defs:
        registry.register(d)
