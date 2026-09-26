"""Underlying workflow dispatch functions for work-request executors (SC-G5-1, #1497).

Handles calling GitHub actions workflow dispatches for:
- CI remediation (Agent-CI-Remediation.yml)
- Issue action (Agent-Issue-Dispatch.yml)
- PR action (Agent-PR-Dispatch.yml)
- Code request (via code_requests.dispatch)
- Assessment (Jules-Assess-Repo.yml)
"""

from __future__ import annotations

import contextlib
import json
import logging
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from code_requests.dispatch_service import CodeDispatch
from code_requests.profiles import AgentProfileStore
from dashboard_config import ORG, REPO_ROOT
from system_utils import run_cmd

log = logging.getLogger("dashboard.staff.work_request_dispatch")


async def _dispatch_workflow_json(
    endpoint: str,
    payload: dict[str, Any],
    prefix: str = "agent-dispatch-",
) -> None:
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


def code_dispatch_from_params(params: dict[str, Any]) -> CodeDispatch:
    """The ``code_request.dispatch`` action params as the shared core's request (#1501)."""
    standards = params.get("standards")
    return CodeDispatch(
        repo=str(params.get("repo") or "").strip(),
        branch=str(params.get("ref") or "main"),
        prompt=str(params.get("prompt") or ""),
        provider=params.get("provider") or None,
        model=params.get("model") or None,
        effort=params.get("effort") or None,
        standards=list(standards) if standards is not None else None,
        budget=params.get("budget") or None,
        profile_id=params.get("profile_id") or None,
    )


async def _dispatch_code_request_workflow(req: CodeDispatch, *, principal: str) -> dict[str, Any]:
    """Dispatch a Code Request through the core the legacy route uses, so it lands in the same history."""
    from code_requests import dispatch_service as service  # noqa: PLC0415

    outcome = await service.run_code_dispatch(
        req,
        principal=principal,
        profile_store=AgentProfileStore(),
        prompt_notes_path=service.PROMPT_NOTES_PATH,
        history_path=service.HISTORY_PATH,
    )
    if outcome.code != 0:
        raise RuntimeError(f"Code request dispatch failed (code {outcome.code}): {outcome.stderr.strip()[:300]}")
    return {
        "status": "dispatched",
        "repository": req.repo,
        "branch": req.branch,
        "provider": outcome.resolved.provider,
        "model": outcome.resolved.model,
        "profile_id": outcome.resolved.profile_id,
        "entry_id": (outcome.entry or {}).get("id", ""),
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
