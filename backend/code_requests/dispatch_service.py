"""The one code-request dispatch core (#1501).

``POST /api/code-requests/dispatch`` and the ``code_request.dispatch`` request
kind both call :func:`run_code_dispatch`, so either path resolves the agent
profile, injects prompt notes and engineering standards server-side, and
records the dispatch in the Code Requests history.
"""

from __future__ import annotations

import asyncio
import datetime as _dt_mod
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config_schema
from code_requests.dispatch import build_full_prompt, trigger_workflow_dispatch
from code_requests.profiles import AgentProfileStore
from system_utils import run_cmd

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017

HISTORY_PATH = Path.home() / "actions-runners" / "dashboard" / "code_requests.json"
PROMPT_NOTES_PATH = Path.home() / "actions-runners" / "dashboard" / "prompt_notes.json"
HISTORY_LIMIT = 200
FALLBACK_PROVIDER = "codex_cli"

# Serialises every writer of the history file, whichever path it came from.
HISTORY_LOCK = asyncio.Lock()

TriggerFn = Callable[..., Awaitable[tuple[int, str]]]


@dataclass(frozen=True)
class CodeDispatch:
    """What the caller asked for; ``None`` settings take the profile's defaults."""

    repo: str
    branch: str
    prompt: str
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    standards: list[str] | None = None
    budget: dict[str, Any] | None = None
    profile_id: str | None = None


@dataclass(frozen=True)
class ResolvedDispatch:
    """The settings the dispatch runs with."""

    provider: str
    model: str
    effort: str | None
    standards: list[str]
    budget: dict[str, Any]
    profile_id: str | None
    profile_snapshot: dict[str, Any] | None


@dataclass(frozen=True)
class DispatchOutcome:
    """``entry`` is the history record, ``None`` when the dispatch was rejected (422) or rate limited (429)."""

    code: int
    stderr: str
    resolved: ResolvedDispatch
    entry: dict[str, Any] | None


def resolve_dispatch(req: CodeDispatch, profile_store: AgentProfileStore) -> ResolvedDispatch:
    """Explicit settings win; the rest come from the named (or default) profile."""
    profile = profile_store.get(req.profile_id) if req.profile_id else profile_store.get_default_or_fallback()
    return ResolvedDispatch(
        provider=req.provider or (profile.provider if profile else FALLBACK_PROVIDER),
        model=req.model or (profile.model if profile else ""),
        effort=req.effort or (profile.effort if profile else None),
        standards=list(req.standards if req.standards is not None else (profile.standards if profile else [])),
        budget=dict(req.budget or (profile.budget if profile else {})),
        profile_id=profile.id if profile else None,
        profile_snapshot=profile.model_dump(mode="json") if profile else None,
    )


def load_prompt_notes(path: Path) -> dict[str, Any]:
    """The operator's prompt notes; enabled and empty when the file is missing or unreadable."""
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {"notes": "", "enabled": True}


def history_entry(req: CodeDispatch, resolved: ResolvedDispatch, code: int, stderr: str) -> dict[str, Any]:
    """The Code Requests history record of one dispatch."""
    now = _dt_mod.datetime.now(UTC)
    entry: dict[str, Any] = {
        "id": str(int(now.timestamp())),
        "repository": req.repo,
        "branch": req.branch,
        "provider": resolved.provider,
        "model": resolved.model,
        "profile_id": resolved.profile_id,
        "profile_snapshot": resolved.profile_snapshot,
        "prompt": req.prompt[:500],
        "standards": resolved.standards,
        "status": "dispatched" if code == 0 else "failed",
        "created_at": now.isoformat(),
    }
    if code != 0:
        entry["error"] = stderr.strip()[:300]
    return entry


async def append_history(path: Path, entry: dict[str, Any]) -> None:
    """Append ``entry`` to the history file, keeping the newest :data:`HISTORY_LIMIT`."""
    async with HISTORY_LOCK:
        try:
            history: list[dict[str, Any]] = []
            if path.exists():
                history = json.loads(path.read_text(encoding="utf-8"))
            history.append(entry)
            config_schema.atomic_write_json(path, history[-HISTORY_LIMIT:])
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass


async def run_code_dispatch(
    req: CodeDispatch,
    *,
    principal: str,
    profile_store: AgentProfileStore,
    prompt_notes_path: Path,
    history_path: Path,
    run_cmd_fn: Any = run_cmd,
    trigger_fn: TriggerFn | None = None,
) -> DispatchOutcome:
    """Resolve, build the full prompt, dispatch, and record the outcome.

    Pre: ``req.repo`` and ``req.branch`` are non-empty.
    Post: the history gains one entry unless the dispatch was rejected (422) or rate limited (429).
    """
    assert req.repo.strip() and req.branch.strip(), "repo and branch are required"  # noqa: S101
    resolved = resolve_dispatch(req, profile_store)
    full_prompt = build_full_prompt(req.prompt, resolved.standards, load_prompt_notes(prompt_notes_path))
    trigger = trigger_fn or trigger_workflow_dispatch
    code, stderr = await trigger(
        req.repo,
        req.branch,
        resolved.provider,
        full_prompt,
        model=resolved.model,
        effort=resolved.effort,
        principal=principal,
        budget=resolved.budget,
        profile_id=resolved.profile_id,
        standards=resolved.standards,
        run_cmd_fn=run_cmd_fn,
    )
    if code in (422, 429):
        return DispatchOutcome(code=code, stderr=stderr, resolved=resolved, entry=None)
    entry = history_entry(req, resolved, code, stderr)
    await append_history(history_path, entry)
    return DispatchOutcome(code=code, stderr=stderr, resolved=resolved, entry=entry)
