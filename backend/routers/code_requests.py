"""Code requests and prompt-settings routes (CR-1, CR-3, issues #1281, #1283)."""

from __future__ import annotations

import asyncio
import datetime as _dt_mod
import json
import logging
import shutil
import time
from pathlib import Path

import config_schema
from code_requests.dispatch_service import (
    HISTORY_LOCK,
    HISTORY_PATH,
    PROMPT_NOTES_PATH,
    CodeDispatch,
    run_code_dispatch,
)
from code_requests.lifecycle import InvalidTransitionError
from code_requests.model import (
    STANDARDS_INJECTION,
    BoardRoute,
    CodeRequest,
    CodeRequestState,
    Requester,
    RequesterKind,
)
from code_requests.profiles import AgentProfileStore
from code_requests.store import CodeRequestStore
from dashboard_config import ORG, REPO_ROOT
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from identity import Principal, require_fleet_peer, require_scope
from input_validation import validate_workflow_inputs
from security import check_dispatch_rate, sanitize_log_value
from system_utils import run_cmd

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard.code_requests")
router = APIRouter(tags=["code_requests"])

# ─── Paths ────────────────────────────────────────────────────────────────────

_CODE_REQUESTS_PATH = HISTORY_PATH
_LEGACY_FEATURE_REQUESTS_PATH = Path.home() / "actions-runners" / "dashboard" / "feature_requests.json"
_MIGRATED_MARKER_PATH = Path.home() / "actions-runners" / "dashboard" / "feature_requests.json.migrated"

# Backwards-compatibility alias for tests and legacy callers (None until overridden)
_FEATURE_REQUESTS_PATH: Path | None = None

_PROMPT_TEMPLATES_PATH = Path.home() / "actions-runners" / "dashboard" / "prompt_templates.json"
_PROMPT_NOTES_PATH = PROMPT_NOTES_PATH


def _active_storage_path() -> Path:
    """Return the active storage path, honoring legacy monkeypatching in tests."""
    if _FEATURE_REQUESTS_PATH is not None:
        return _FEATURE_REQUESTS_PATH
    return _CODE_REQUESTS_PATH


# ─── Dispatch target ──────────────────────────────────────────────────────────

_DISPATCH_WORKFLOW = "Jules-Feature-Request.yml"
_DISPATCH_WORKFLOW_ENDPOINT = f"/repos/{ORG}/Repository_Management/actions/workflows/{_DISPATCH_WORKFLOW}"
_DISPATCH_TARGET_TTL_S = 600.0

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

_code_requests_lock: asyncio.Lock = HISTORY_LOCK
_prompt_templates_lock: asyncio.Lock = asyncio.Lock()
_prompt_notes_lock: asyncio.Lock = asyncio.Lock()


# ─── Storage migration ────────────────────────────────────────────────────────


def _migrate_storage_if_needed() -> None:
    if _MIGRATED_MARKER_PATH.exists():
        return
    try:
        src = _FEATURE_REQUESTS_PATH if _FEATURE_REQUESTS_PATH is not None else _LEGACY_FEATURE_REQUESTS_PATH
        if src.exists() and not _CODE_REQUESTS_PATH.exists():
            _CODE_REQUESTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, _CODE_REQUESTS_PATH)
        if src.exists():
            _MIGRATED_MARKER_PATH.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    except OSError as e:
        log.warning("Storage migration error: %s", e)


# ─── Store Instance ───────────────────────────────────────────────────────────

_store: CodeRequestStore | None = None


def _get_store() -> CodeRequestStore:
    global _store
    storage_path = _active_storage_path()
    if _store is None or _store.cache_path != storage_path:
        _store = CodeRequestStore(cache_path=storage_path)
    return _store


_profile_store: AgentProfileStore = AgentProfileStore()


def _get_profile_store() -> AgentProfileStore:
    return _profile_store


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("/api/code-requests")
async def list_code_requests(_peer: str = Depends(require_fleet_peer)) -> dict:  # noqa: B008
    """List saved code implementation requests."""
    _migrate_storage_if_needed()
    store = _get_store()
    items = await store.list()
    serialized = [item.model_dump(mode="json") for item in items]
    return {
        "requests": serialized,
        "total": len(serialized),
        "dispatchTarget": await _dispatch_target_status(),
    }


@router.get("/api/code-requests/templates")
async def list_prompt_templates() -> dict:
    """List saved prompt templates and global prompt notes."""
    try:
        t_data = (
            json.loads(_PROMPT_TEMPLATES_PATH.read_text(encoding="utf-8")) if _PROMPT_TEMPLATES_PATH.exists() else []
        )
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        t_data = []
    try:
        pn_data = (
            json.loads(_PROMPT_NOTES_PATH.read_text(encoding="utf-8"))
            if _PROMPT_NOTES_PATH.exists()
            else {"notes": "", "enabled": True}
        )
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pn_data = {"notes": "", "enabled": True}
    return {"templates": t_data, "standards": STANDARDS_INJECTION, "promptNotes": pn_data}


@router.post("/api/code-requests/templates")
async def save_prompt_template(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict:
    """Save a prompt template."""
    body = await request.json()
    name, content = str(body.get("name", "")).strip(), str(body.get("content", "")).strip()
    if not name or not content:
        raise HTTPException(status_code=422, detail="name and content required")
    async with _prompt_templates_lock:
        try:
            templates = (
                json.loads(_PROMPT_TEMPLATES_PATH.read_text(encoding="utf-8"))
                if _PROMPT_TEMPLATES_PATH.exists()
                else []
            )
            templates = [t for t in templates if t.get("name") != name]
            templates.append({"name": name, "content": content, "updated_at": datetime.now(UTC).isoformat()})
            config_schema.atomic_write_json(_PROMPT_TEMPLATES_PATH, templates)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e
    return {"status": "saved", "name": name}


@router.get("/api/code-requests/{id}")
async def get_code_request(
    id: str,
    _peer: str = Depends(require_fleet_peer),  # noqa: B008
) -> dict:
    """Get a Code Request by id or issue number."""
    _migrate_storage_if_needed()
    store = _get_store()
    item = await store.get(id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Code request {id!r} not found")
    return item.model_dump(mode="json")


@router.post("/api/code-requests")
async def create_code_request(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict:
    """Create a new Code Request in draft or triage state."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")

    repo = str(body.get("repository", "")).strip()
    prompt = str(body.get("prompt", "")).strip()
    if not repo:
        raise HTTPException(status_code=422, detail="repository required")
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt required")

    state_str = str(body.get("state", "")).strip().lower()
    initial_state = (
        CodeRequestState.TRIAGE if (body.get("submitted") or state_str == "triage") else CodeRequestState.DRAFT
    )

    raw_req = body.get("requester")
    req_dict: dict = raw_req if isinstance(raw_req, dict) else {}
    req_id = str(req_dict.get("id") or principal.id)
    is_agent = (req_dict.get("kind") == "agent") or (not req_dict and getattr(principal, "type", "").lower() == "bot")
    requester = Requester(id=req_id, kind=RequesterKind.AGENT if is_agent else RequesterKind.HUMAN)

    try:
        board_route = BoardRoute(str(body.get("board_route", BoardRoute.AUTO.value)).lower())
    except ValueError:
        board_route = BoardRoute.AUTO

    now = datetime.now(UTC).isoformat()
    req_id = str(body.get("id") or f"cr-unknown-{int(datetime.now(UTC).timestamp())}")
    title = str(body.get("title") or f"Code Request: {prompt[:40]}")

    planner_profile_id = body.get("planner_profile_id")
    p_obj = _get_profile_store().get(planner_profile_id) if planner_profile_id else None
    profile_snapshot = p_obj.model_dump(mode="json") if p_obj else None

    code_req = CodeRequest(
        id=req_id,
        repository=repo,
        title=title,
        state=initial_state,
        prompt=prompt,
        requester=requester,
        planner_profile_id=planner_profile_id,
        executor_profile_id=body.get("executor_profile_id"),
        profile_snapshot=profile_snapshot,
        board_route=board_route,
        board_proposal=body.get("board_proposal"),
        plan_epic=body.get("plan_epic"),
        branch=str(body.get("branch", "main")),
        standards=list(body.get("standards", []) or []),
        created_at=now,
        updated_at=now,
    )

    store = _get_store()
    saved = await store.create(code_req)
    return saved.model_dump(mode="json")


@router.post("/api/code-requests/{id}/transition")
async def transition_code_request(
    id: str,
    request: Request,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict:
    """Transition a Code Request lifecycle state."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")

    to_state = str(body.get("to_state", "")).strip()
    reason = str(body.get("reason", "")).strip()
    is_override = bool(body.get("is_operator_override", False) or body.get("override", False))

    if not to_state or not reason:
        raise HTTPException(status_code=422, detail="to_state and reason required")

    store = _get_store()
    try:
        actor = principal.id or principal.name or "operator"
        updated = await store.transition(
            id,
            to_state,
            actor=actor,
            reason=reason,
            is_operator_override=is_override,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return updated.model_dump(mode="json")


@router.get("/api/settings/prompt-notes")
async def get_prompt_notes() -> dict:
    """Get the global prompt notes that are automatically injected into every prompt."""
    try:
        if _PROMPT_NOTES_PATH.exists():
            return json.loads(_PROMPT_NOTES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {"notes": "", "enabled": True}


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
    notes, enabled = str(body.get("notes", "")).strip(), bool(body.get("enabled", True))
    async with _prompt_notes_lock:
        try:
            config_schema.atomic_write_json(_PROMPT_NOTES_PATH, {"notes": notes, "enabled": enabled})
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
    prompt = str(body.get("prompt", "")).strip()
    template_id = str(body.get("template_id", "")).strip()
    if not repo:
        raise HTTPException(status_code=422, detail="repository required")
    if not prompt and not template_id:
        raise HTTPException(status_code=422, detail="prompt or template_id required")

    raw_st = body.get("standards")
    req = CodeDispatch(
        repo=repo,
        branch=branch,
        prompt=prompt,
        provider=str(body.get("provider", "")).strip() or None,
        model=str(body.get("model", "")).strip() or None,
        effort=body.get("effort") or None,
        standards=list(raw_st) if raw_st is not None else None,
        budget=body.get("budget") or None,
        profile_id=str(body.get("profile_id", "")).strip() or None,
    )
    log.info(
        "audit: code_request_dispatch repo=%s provider=%s branch=%s",
        sanitize_log_value(repo),
        sanitize_log_value(req.provider or "(profile)"),
        sanitize_log_value(branch),
    )
    _migrate_storage_if_needed()
    outcome = await run_code_dispatch(
        req,
        principal=principal.id,
        profile_store=_get_profile_store(),
        prompt_notes_path=_PROMPT_NOTES_PATH,
        history_path=_active_storage_path(),
        run_cmd_fn=run_cmd,
    )
    code, stderr, resolved = outcome.code, outcome.stderr, outcome.resolved
    _record_dispatch_target(code == 0, stderr)
    if code in (422, 429):
        raise HTTPException(status_code=code, detail=stderr)
    if code != 0:
        log.warning("code_request_dispatch failed: %s", sanitize_log_value(stderr.strip()[:200]))
        detail_msg = str(_dispatch_target_state["detail"] or stderr)
        raise HTTPException(status_code=502, detail=detail_msg)
    return {
        "status": "dispatched",
        "repository": repo,
        "provider": resolved.provider,
        "model": resolved.model,
        "profile_id": resolved.profile_id,
        "entry_id": (outcome.entry or {}).get("id", ""),
    }


# ─── Backward-compatibility aliases for /api/feature-requests* ───────────────


def _add_deprecation_headers(response: Response, successor: str) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = f'<{successor}>; rel="successor-version"'


@router.get("/api/feature-requests", deprecated=True)
async def list_feature_requests_deprecated(response: Response) -> dict:
    _add_deprecation_headers(response, "/api/code-requests")
    return await list_code_requests()


@router.get("/api/feature-requests/templates", deprecated=True)
async def list_prompt_templates_deprecated(response: Response) -> dict:
    _add_deprecation_headers(response, "/api/code-requests/templates")
    return await list_prompt_templates()


@router.post("/api/feature-requests/templates", deprecated=True)
async def save_prompt_template_deprecated(
    request: Request,
    response: Response,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict:
    _add_deprecation_headers(response, "/api/code-requests/templates")
    return await save_prompt_template(request, principal=principal)


@router.post("/api/feature-requests/dispatch", deprecated=True)
async def dispatch_feature_request_deprecated(
    request: Request,
    response: Response,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict:
    _add_deprecation_headers(response, "/api/code-requests/dispatch")
    return await dispatch_code_request(request, principal=principal)
