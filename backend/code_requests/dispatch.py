"""Code Request workflow dispatch helper (CR-1 / CR-2 / CR-3, issues #1280, #1281, #1282, #1283)."""

from __future__ import annotations

import logging
from typing import Any

from code_requests.model import STANDARDS_INJECTION
from dashboard_config import ORG, REPO_ROOT
from system_utils import run_cmd

log = logging.getLogger("dashboard.code_requests.dispatch")


def build_full_prompt(prompt: str, standards: list[str], prompt_notes_data: dict[str, Any]) -> str:
    """Compose user prompt with prompt notes and standards injection."""
    full_prompt = prompt
    notes_val = str(prompt_notes_data.get("notes", ""))
    if prompt_notes_data.get("enabled", True) and notes_val.strip():
        full_prompt = f"{notes_val}\n\n{prompt}"

    injected_standards = "\n\n".join(
        f"[{s.upper()}] {STANDARDS_INJECTION[s]}" for s in standards if s in STANDARDS_INJECTION
    )
    if injected_standards:
        full_prompt = f"{full_prompt}\n\n## Engineering Standards\n{injected_standards}"

    return full_prompt


async def trigger_workflow_dispatch(
    repo: str,
    branch: str,
    provider: str,
    full_prompt: str,
    *,
    model: str = "",
    effort: str | None = None,
    principal: str = "",
    budget: dict[str, Any] | None = None,
    profile_id: str | None = None,
    standards: list[str] | None = None,
    run_cmd_fn: Any = run_cmd,
) -> tuple[int, str]:
    """Dispatch an agent task via RD's agent_dispatch_router and command envelope."""
    from code_requests.agent_dispatcher import dispatch_code_request

    code, stderr, _envelope = await dispatch_code_request(
        repository=repo,
        branch=branch,
        provider=provider,
        prompt=full_prompt,
        model=model,
        effort=effort,
        principal=principal,
        budget=budget,
        profile_id=profile_id,
        standards=standards,
        run_cmd_fn=run_cmd_fn,
        org=ORG,
        repo_root=REPO_ROOT,
    )
    return code, stderr
