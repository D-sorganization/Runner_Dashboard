"""Run planning: resolve role, provider, and prompt without side effects."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from typing import Any

from staff import consolidation, workspace
from staff import focus as focus_mod
from staff import runner_ops as ops
from staff.adapters import ProviderAdapter
from staff.lessons import select_repo_lessons
from staff.plan import RunPlan, RunRequest
from staff.roles import RoleSpec
from staff.standards_registry import TOTAL_CONTEXT_TOKEN_CAP, estimate_tokens

_SAFE_REF = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")


def build_run_plan(
    req: RunRequest,
    *,
    roles: dict[str, RoleSpec],
    adapters: dict[str, ProviderAdapter],
    machine: str,
    focus_loader: Callable[[], list[dict[str, Any]]] | None,
) -> RunPlan:
    """Resolve role + provider + prompt without side effects (raises ValueError on bad input)."""
    from library.dispatch_hook import library_references_for_plan
    from staff.cloud_dispatch import resolve_plan_execution
    from staff.dispatch_bundle import DispatchContractError, resolve_dispatch_bundle
    from staff.failover_dispatch import resolve_provider_and_model
    from staff.failover_model import resolve_launch_model, role_base_tier
    from staff.provider_failover_config import resolve_failover_chain
    from staff.role_dispatch import resolve_dispatch_role

    role = resolve_dispatch_role(roles, req)

    provider, _chain_model, model_tier, auth_skipped = resolve_provider_and_model(adapters, machine, req, role)
    if req.repo and not _SAFE_REF.match(req.repo):
        raise ValueError("repo must be a bare repository name")
    if role.repos and req.repo and req.repo not in role.repos:
        raise ValueError(f"role '{req.role}' is scoped to {', '.join(role.repos)}")
    if not (req.issue or req.pr or req.prompt.strip()):
        raise ValueError("one of issue, pr or prompt is required")
    branch = f"staff/{role.name}-{req.issue or req.pr or 'task'}-{uuid.uuid4().hex[:6]}"
    lease = bool(role.permissions.get("lease", True)) and bool(req.issue) and bool(req.repo)
    paragraph = consolidation.prompt_paragraph(req.consolidation) if req.consolidation else ""
    focus = focus_mod.focus_paragraph(req.repo, focus_mod.load_items(focus_loader)) if req.repo else ""
    try:
        bundle = resolve_dispatch_bundle(
            role=role.name,
            repo=req.repo,
            task_type=req.task_type,
            standards_extra=req.standards_extra,
            requested_skill=req.requested_skill,
        )
    except DispatchContractError as exc:
        raise ValueError(str(exc)) from exc
    repo_lessons = select_repo_lessons(req.repo)
    references_block, library_work_ids = library_references_for_plan(role, req)
    prompt_token_cap = TOTAL_CONTEXT_TOKEN_CAP if provider == "codex" else None
    prompt = workspace.compose_prompt(
        role,
        repo=req.repo,
        target_ref=req.target_ref,
        operator_prompt=req.prompt,
        branch=branch,
        consolidation=paragraph,
        focus=focus,
        dispatch_bundle=bundle,
        library_references=references_block,
        repo_lessons_result=repo_lessons,
        prompt_token_cap=prompt_token_cap,
    )
    adapter = adapters[provider]
    chain = resolve_failover_chain(role, requested_provider=provider if req.provider else None)
    step = next((s for s in chain if s.provider == provider), None)
    effective_model, resolved_tier = resolve_launch_model(
        req=req,
        role=role,
        provider=provider,
        adapter=adapter,
        step=step,
        active_tier=role_base_tier(role),
    )
    execution_mode, argv, cloud_stdin, lease = resolve_plan_execution(
        req, role, provider, adapter, prompt, effective_model, lease, ops.read_only_kwargs(role)
    )
    return RunPlan(
        role=role.name,
        provider=provider,
        model=effective_model,
        model_tier=resolved_tier or model_tier,
        repo=req.repo,
        target_kind=req.target_kind,
        target_ref=req.target_ref,
        operator_prompt=req.prompt,
        prompt=prompt,
        argv=argv,
        branch=branch,
        lease_ritual=lease,
        consolidation=dict(req.consolidation) if req.consolidation else None,
        focus=focus,
        pr_lifecycle=req.pr_lifecycle,
        head_ref=req.head_ref,
        skill_pin=bundle.skill_pin,
        standards_version=bundle.standards_version,
        standards_tokens=bundle.standards_tokens or estimate_tokens(bundle.standards_block),
        context_stamp=json.dumps(bundle.context_stamp(prompt=prompt, lessons_tokens=repo_lessons.tokens)),
        library_work_ids=library_work_ids,
        injected_lesson_ids=repo_lessons.injected_ids,
        skipped_lesson_ids=repo_lessons.skipped_ids,
        lessons_tokens=repo_lessons.tokens,
        provider_auth_skipped=tuple(auth_skipped),
        execution_mode=execution_mode,
        cloud_stdin=cloud_stdin,
    )
