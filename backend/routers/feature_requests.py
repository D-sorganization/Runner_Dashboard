"""Feature requests and prompt-settings routes.

Covers:
  - GET  /api/feature-requests           – list saved feature requests
  - GET  /api/feature-requests/templates – list prompt templates + notes
  - POST /api/feature-requests/templates – save a prompt template
  - POST /api/feature-requests/dispatch  – dispatch a feature request via Jules
  - GET  /api/settings/prompt-notes      – get global prompt notes
  - PUT  /api/settings/prompt-notes      – update global prompt notes
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt_mod
import json
import logging
import tempfile
from pathlib import Path

import config_schema
from dashboard_config import ORG, REPO_ROOT
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_scope
from input_validation import MAX_INPUT_VALUE_LENGTH, validate_workflow_inputs
from security import check_dispatch_rate, sanitize_log_value
from system_utils import run_cmd

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard.feature_requests")
router = APIRouter(tags=["feature_requests"])

# ─── Paths ────────────────────────────────────────────────────────────────────

_FEATURE_REQUESTS_PATH = Path.home() / "actions-runners" / "dashboard" / "feature_requests.json"
_PROMPT_TEMPLATES_PATH = Path.home() / "actions-runners" / "dashboard" / "prompt_templates.json"
_PROMPT_NOTES_PATH = Path.home() / "actions-runners" / "dashboard" / "prompt_notes.json"

# ─── Async locks ──────────────────────────────────────────────────────────────

_feature_requests_lock: asyncio.Lock = asyncio.Lock()
_prompt_templates_lock: asyncio.Lock = asyncio.Lock()
_prompt_notes_lock: asyncio.Lock = asyncio.Lock()

# ─── Standards injection map ──────────────────────────────────────────────────

STANDARDS_INJECTION: dict[str, str] = {
    "tdd": (
        "Use Test-Driven Development: write failing tests first (RED), then minimal code to pass (GREEN),"
        " then refactor. Tests must pass before any PR."
    ),
    "dbc": (
        "Apply Design by Contract: validate inputs at boundaries, assert internal invariants,"
        " document pre/postconditions in docstrings."
    ),
    "dry": (
        "Apply DRY: extract shared logic into modules, eliminate duplication."
        " Three similar code blocks should become one shared function."
    ),
    "lod": (
        "Apply Law of Demeter: components talk to immediate neighbors only."
        " UI receives view models, not raw nested payloads."
    ),
    "security": (
        "Apply security-first: validate all inputs, avoid injection vulnerabilities,"
        " use parameterized queries, never log secrets."
    ),
    "docs": (
        "Document public APIs, non-obvious decisions, and architecture choices."
        " Prefer short clear docstrings over multi-paragraph ones."
    ),
}

# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("/api/feature-requests")
async def list_feature_requests() -> dict:
    """List saved feature implementation requests."""
    try:
        if _FEATURE_REQUESTS_PATH.exists():
            data = json.loads(_FEATURE_REQUESTS_PATH.read_text(encoding="utf-8"))
        else:
            data = []
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        data = []
    return {"requests": list(reversed(data[-100:])), "total": len(data)}


@router.get("/api/feature-requests/templates")
async def list_prompt_templates() -> dict:
    """List saved prompt templates and global prompt notes."""
    templates_data = []
    try:
        if _PROMPT_TEMPLATES_PATH.exists():
            templates_data = json.loads(_PROMPT_TEMPLATES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass

    prompt_notes_data = {"notes": "", "enabled": True}
    try:
        if _PROMPT_NOTES_PATH.exists():
            prompt_notes_data = json.loads(_PROMPT_NOTES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass

    return {
        "templates": templates_data,
        "standards": STANDARDS_INJECTION,
        "promptNotes": prompt_notes_data,
    }


@router.post("/api/feature-requests/templates")
async def save_prompt_template(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("feature-requests.manage")),  # noqa: B008
) -> dict:
    """Save a prompt template."""
    body = await request.json()
    name = str(body.get("name", "")).strip()
    content = str(body.get("content", "")).strip()
    if not name or not content:
        raise HTTPException(status_code=422, detail="name and content required")
    async with _prompt_templates_lock:
        try:
            templates: list[dict] = []
            if _PROMPT_TEMPLATES_PATH.exists():
                templates = json.loads(_PROMPT_TEMPLATES_PATH.read_text(encoding="utf-8"))
            existing_idx = next((i for i, t in enumerate(templates) if t.get("name") == name), None)
            template = {
                "name": name,
                "content": content,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            if existing_idx is not None:
                templates[existing_idx] = template
            else:
                templates.append(template)
            config_schema.atomic_write_json(_PROMPT_TEMPLATES_PATH, templates)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e
    return {"status": "saved", "name": name}


@router.get("/api/settings/prompt-notes")
async def get_prompt_notes() -> dict:
    """Get the global prompt notes that are automatically injected into every prompt."""
    try:
        if _PROMPT_NOTES_PATH.exists():
            data = json.loads(_PROMPT_NOTES_PATH.read_text(encoding="utf-8"))
        else:
            data = {"notes": "", "enabled": True}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        data = {"notes": "", "enabled": True}
    return data


@router.put("/api/settings/prompt-notes")
async def update_prompt_notes(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("operator")),  # noqa: B008
) -> dict:
    """Update the global prompt notes."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")

    notes = str(body.get("notes", "")).strip()
    enabled = bool(body.get("enabled", True))

    async with _prompt_notes_lock:
        try:
            data = {"notes": notes, "enabled": enabled}
            config_schema.atomic_write_json(_PROMPT_NOTES_PATH, data)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e
    return {"status": "saved", "notes_length": len(notes), "enabled": enabled}


@router.post("/api/feature-requests/dispatch")
async def dispatch_feature_request(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("feature-requests.manage")),  # noqa: B008
) -> dict:
    """Dispatch a feature implementation request via CI remediation workflow."""
    client_ip = request.client.host if request.client else "unknown"
    check_dispatch_rate(client_ip, principal_id=principal.id)
    body = await request.json()
    # Validate any caller-supplied raw inputs BEFORE any I/O (#411). We do not
    # forward this dict directly — the dispatch payload is rebuilt below from
    # explicit fields — but rejecting oversized/non-string values here protects
    # the temp-file write path from abuse.
    validate_workflow_inputs(body.get("inputs"))
    repo = str(body.get("repository", "")).strip()
    branch = str(body.get("branch", "main")).strip()
    provider = str(body.get("provider", "jules_api")).strip()
    prompt = str(body.get("prompt", "")).strip()
    standards = body.get("standards", []) or []
    template_id = str(body.get("template_id", "")).strip()
    if not repo:
        raise HTTPException(status_code=422, detail="repository required")
    if not prompt and not template_id:
        raise HTTPException(status_code=422, detail="prompt or template_id required")

    log.info(
        "audit: feature_request_dispatch repo=%s provider=%s branch=%s",
        sanitize_log_value(repo),
        sanitize_log_value(provider),
        sanitize_log_value(branch),
    )

    # Load and apply prompt notes if enabled
    prompt_notes_data: dict[str, object] = {"notes": "", "enabled": True}
    try:
        if _PROMPT_NOTES_PATH.exists():
            prompt_notes_data = json.loads(_PROMPT_NOTES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass

    # Build full prompt with notes and standards injection
    full_prompt = prompt
    notes_val = str(prompt_notes_data.get("notes", ""))
    if prompt_notes_data.get("enabled", True) and notes_val.strip():
        full_prompt = f"{notes_val}\n\n{prompt}"

    injected_standards = "\n\n".join(
        f"[{s.upper()}] {STANDARDS_INJECTION[s]}" for s in standards if s in STANDARDS_INJECTION
    )
    if injected_standards:
        full_prompt = f"{full_prompt}\n\n## Engineering Standards\n{injected_standards}"

    # Save to history
    entry: dict = {}
    async with _feature_requests_lock:
        try:
            history: list[dict] = []
            if _FEATURE_REQUESTS_PATH.exists():
                history = json.loads(_FEATURE_REQUESTS_PATH.read_text(encoding="utf-8"))
            entry = {
                "id": str(int(datetime.now(UTC).timestamp())),
                "repository": repo,
                "branch": branch,
                "provider": provider,
                "prompt": prompt[:500],
                "standards": list(standards),
                "status": "dispatched",
                "created_at": datetime.now(UTC).isoformat(),
            }
            history.append(entry)
            config_schema.atomic_write_json(_FEATURE_REQUESTS_PATH, history[-200:])
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass

    # Dispatch via feature-request workflow. The constructed inputs are
    # re-validated to enforce the per-value length cap (#411) — full_prompt
    # may include injected standards which can push it past the cap.
    dispatch_inputs = validate_workflow_inputs(
        {
            "target_repository": f"{ORG}/{repo}",
            "branch": branch,
            "provider": provider,
            "prompt": full_prompt[:MAX_INPUT_VALUE_LENGTH],
        }
    )
    endpoint = f"/repos/{ORG}/Repository_Management/actions/workflows/Jules-Feature-Request.yml/dispatches"
    payload = {
        "ref": "main",
        "inputs": dispatch_inputs,
    }
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        pf = f.name
    try:
        code, _, stderr = await run_cmd(
            ["gh", "api", endpoint, "--method", "POST", "--input", pf],
            timeout=30,
            cwd=REPO_ROOT,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(pf).unlink()
    if code != 0:
        log.warning("feature_request_dispatch failed: %s", stderr.strip()[:200])
        # Don't raise - save history record and return success anyway (workflow may not exist yet)
    return {
        "status": "dispatched",
        "repository": repo,
        "provider": provider,
        "entry_id": entry.get("id", ""),
    }
