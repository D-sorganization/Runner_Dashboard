"""Code Request workflow dispatch helper (CR-1 / CR-2, issues #1280, #1281, #1282)."""

from __future__ import annotations

import contextlib
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any

from code_requests.model import STANDARDS_INJECTION
from dashboard_config import ORG, REPO_ROOT
from input_validation import MAX_INPUT_VALUE_LENGTH, validate_workflow_inputs
from system_utils import run_cmd

log = logging.getLogger("dashboard.code_requests.dispatch")

_DISPATCH_WORKFLOW = "Jules-Feature-Request.yml"
_DISPATCH_WORKFLOW_ENDPOINT = f"/repos/{ORG}/Repository_Management/actions/workflows/{_DISPATCH_WORKFLOW}"
_DISPATCH_TARGET_TTL_S = 600.0

_dispatch_target_state: dict[str, Any] = {"checked_at": None, "available": None, "detail": ""}


def _record_dispatch_target(available: bool, stderr: str = "") -> None:
    detail = "" if available else f"dispatch target unavailable: {_DISPATCH_WORKFLOW} — {stderr.strip()[:200]}"
    _dispatch_target_state.update(checked_at=time.monotonic(), available=available, detail=detail)


async def dispatch_target_status() -> dict[str, Any]:
    """Return whether the dispatch workflow exists, probing at most once per TTL."""
    checked_at = _dispatch_target_state["checked_at"]
    if not isinstance(checked_at, float) or time.monotonic() - checked_at > _DISPATCH_TARGET_TTL_S:
        code, _, stderr = await run_cmd(
            ["gh", "api", _DISPATCH_WORKFLOW_ENDPOINT, "--silent"],
            timeout=15,
            cwd=REPO_ROOT,
        )
        _record_dispatch_target(code == 0, stderr)
    return {
        "workflow": _DISPATCH_WORKFLOW,
        "available": _dispatch_target_state["available"],
        "detail": _dispatch_target_state["detail"],
    }


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
    run_cmd_fn: Any = run_cmd,
) -> tuple[int, str]:
    """Invoke GitHub Actions workflow dispatch via gh CLI."""
    dispatch_inputs = validate_workflow_inputs(
        {
            "target_repository": f"{ORG}/{repo}",
            "branch": branch,
            "provider": provider,
            "prompt": full_prompt[:MAX_INPUT_VALUE_LENGTH],
        }
    )
    endpoint = f"{_DISPATCH_WORKFLOW_ENDPOINT}/dispatches"
    payload = {
        "ref": "main",
        "inputs": dispatch_inputs,
    }
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        pf = f.name
    try:
        code, _, stderr = await run_cmd_fn(
            ["gh", "api", endpoint, "--method", "POST", "--input", pf],
            timeout=30,
            cwd=REPO_ROOT,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(pf).unlink()

    _record_dispatch_target(code == 0, stderr)
    return code, stderr
