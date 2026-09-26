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
