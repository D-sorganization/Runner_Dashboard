"""Code requests and prompt-settings routes (CR-1, issue #1281).

Covers:
  - GET  /api/code-requests           – list saved code requests
  - GET  /api/code-requests/templates – list prompt templates + notes
  - POST /api/code-requests/templates – save a prompt template
  - POST /api/code-requests/dispatch  – dispatch a code request via Jules / runner
  - GET  /api/settings/prompt-notes   – get global prompt notes
  - PUT  /api/settings/prompt-notes   – update global prompt notes

Backward compatibility:
  - /api/feature-requests* endpoints are preserved as deprecated aliases with
    Deprecation: true and Link: </api/code-requests*>; rel="successor-version".
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt_mod
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path

import config_schema
from dashboard_config import ORG, REPO_ROOT
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from identity import Principal, require_scope
from input_validation import MAX_INPUT_VALUE_LENGTH, validate_workflow_inputs
from security import check_dispatch_rate, sanitize_log_value
from system_utils import run_cmd

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard.code_requests")
router = APIRouter(tags=["code_requests"])

# ─── Paths ────────────────────────────────────────────────────────────────────

_CODE_REQUESTS_PATH = Path.home() / "actions-runners" / "dashboard" / "code_requests.json"
_LEGACY_FEATURE_REQUESTS_PATH = Path.home() / "actions-runners" / "dashboard" / "feature_requests.json"
_MIGRATED_MARKER_PATH = Path.home() / "actions-runners" / "dashboard" / "feature_requests.json.migrated"

# Backwards-compatibility alias for tests and legacy callers (None until overridden)
_FEATURE_REQUESTS_PATH: Path | None = None

_PROMPT_TEMPLATES_PATH = Path.home() / "actions-runners" / "dashboard" / "prompt_templates.json"
_PROMPT_NOTES_PATH = Path.home() / "actions-runners" / "dashboard" / "prompt_notes.json"


def _active_storage_path() -> Path:
    """Return the active storage path, honoring legacy monkeypatching in tests."""
    if _FEATURE_REQUESTS_PATH is not None:
        return _FEATURE_REQUESTS_PATH
    return _CODE_REQUESTS_PATH


# ─── Dispatch target ──────────────────────────────────────────────────────────

_DISPATCH_WORKFLOW = "Jules-Feature-Request.yml"
_DISPATCH_WORKFLOW_ENDPOINT = f"/repos/{ORG}/Repository_Management/actions/workflows/{_DISPATCH_WORKFLOW}"
_DISPATCH_TARGET_TTL_S = 600.0

# Cached result of probing the dispatch workflow (#1280). A failed dispatch
# primes it too, so the UI can disable dispatch without another API call.
_dispatch_target_state: dict[str, object] = {"checked_at": None, "available": None, "detail": ""}


def _record_dispatch_target(available: bool, stderr: str = "") -> None:
    detail = "" if available else f"dispatch target unavailable: {_DISPATCH_WORKFLOW} — {stderr.strip()[:200]}"
    _dispatch_target_state.update(checked_at=time.monotonic(), available=available, detail=detail)


async def _dispatch_target_status() -> dict[str, object]:
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


# ─── Async locks ──────────────────────────────────────────────────────────────

_code_requests_lock: asyncio.Lock = asyncio.Lock()
_prompt_templates_lock: asyncio.Lock = asyncio.Lock()
_prompt_notes_lock: asyncio.Lock = asyncio.Lock()


# ─── Storage migration ────────────────────────────────────────────────────────


def _migrate_storage_if_needed() -> None:
    """Migrate legacy feature_requests.json to code_requests.json on first read.

    Copies the file and leaves a .migrated marker; never deletes the original.
    """
    if _MIGRATED_MARKER_PATH.exists():
        return
    try:
        source_path = _FEATURE_REQUESTS_PATH if _FEATURE_REQUESTS_PATH is not None else _LEGACY_FEATURE_REQUESTS_PATH
        target_path = _CODE_REQUESTS_PATH
        if source_path.exists():
            if not target_path.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)
            _MIGRATED_MARKER_PATH.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    except OSError as e:
        log.warning("Storage migration error: %s", e)


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


@router.get("/api/code-requests")
async def list_code_requests() -> dict:
    """List saved code implementation requests."""
    _migrate_storage_if_needed()
    path = _active_storage_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = []
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        data = []
    return {
        "requests": list(reversed(data[-100:])),
        "total": len(data),
        "dispatchTarget": await _dispatch_target_status(),
    }


@router.get("/api/code-requests/templates")
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


@router.post("/api/code-requests/templates")
async def save_prompt_template(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
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


@router.post("/api/code-requests/dispatch")
async def dispatch_code_request(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict:
    """Dispatch a code implementation request via CI remediation workflow."""
    client_ip = request.client.host if request.client else "unknown"
    check_dispatch_rate(client_ip, principal_id=principal.id)
    body = await request.json()
    # Validate any caller-supplied raw inputs BEFORE any I/O (#411).
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
        "audit: code_request_dispatch repo=%s provider=%s branch=%s",
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
        code, _, stderr = await run_cmd(
            ["gh", "api", endpoint, "--method", "POST", "--input", pf],
            timeout=30,
            cwd=REPO_ROOT,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(pf).unlink()
    _record_dispatch_target(code == 0, stderr)
    if code != 0:
        log.warning("code_request_dispatch failed: %s", sanitize_log_value(stderr.strip()[:200]))

    # Save to history only once the real outcome is known (#1280).
    entry: dict = {
        "id": str(int(datetime.now(UTC).timestamp())),
        "repository": repo,
        "branch": branch,
        "provider": provider,
        "prompt": prompt[:500],
        "standards": list(standards),
        "status": "dispatched" if code == 0 else "failed",
        "created_at": datetime.now(UTC).isoformat(),
    }
    if code != 0:
        entry["error"] = stderr.strip()[:300]
    _migrate_storage_if_needed()
    path = _active_storage_path()
    async with _code_requests_lock:
        try:
            history: list[dict] = []
            if path.exists():
                history = json.loads(path.read_text(encoding="utf-8"))
            history.append(entry)
            config_schema.atomic_write_json(path, history[-200:])
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass

    if code != 0:
        raise HTTPException(status_code=502, detail=str(_dispatch_target_state["detail"]))
    return {
        "status": "dispatched",
        "repository": repo,
        "provider": provider,
        "entry_id": entry.get("id", ""),
    }


# ─── Backward-compatibility aliases for /api/feature-requests* ───────────────


def _add_deprecation_headers(response: Response, successor: str) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = f'<{successor}>; rel="successor-version"'


@router.get("/api/feature-requests", deprecated=True)
async def list_feature_requests_deprecated(response: Response) -> dict:
    """Deprecated alias for GET /api/code-requests."""
    _add_deprecation_headers(response, "/api/code-requests")
    return await list_code_requests()


@router.get("/api/feature-requests/templates", deprecated=True)
async def list_prompt_templates_deprecated(response: Response) -> dict:
    """Deprecated alias for GET /api/code-requests/templates."""
    _add_deprecation_headers(response, "/api/code-requests/templates")
    return await list_prompt_templates()


@router.post("/api/feature-requests/templates", deprecated=True)
async def save_prompt_template_deprecated(
    request: Request,
    response: Response,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict:
    """Deprecated alias for POST /api/code-requests/templates."""
    _add_deprecation_headers(response, "/api/code-requests/templates")
    return await save_prompt_template(request, principal=principal)


@router.post("/api/feature-requests/dispatch", deprecated=True)
async def dispatch_feature_request_deprecated(
    request: Request,
    response: Response,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict:
    """Deprecated alias for POST /api/code-requests/dispatch."""
    _add_deprecation_headers(response, "/api/code-requests/dispatch")
    return await dispatch_code_request(request, principal=principal)
