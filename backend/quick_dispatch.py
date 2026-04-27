"""Quick-dispatch endpoint logic for ad-hoc agent tasks.

Provides a single-call surface for triggering the Agent-Quick-Dispatch workflow
on Repository_Management.  Rate-limited to 10 calls per 60-second window
(in-process token bucket) and writes an audit log entry to disk after each
accepted dispatch.
"""

from __future__ import annotations

import asyncio
import datetime as _dt_mod
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import agent_remediation
import config_schema
import quota_enforcement
from dispatch_contract import DispatchAccess
from identity import identity_manager
from pydantic import BaseModel, Field

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017

log = logging.getLogger("dashboard.quick_dispatch")

# ─── History path ─────────────────────────────────────────────────────────────

_QUICK_DISPATCH_HISTORY_PATH = Path(os.environ.get("QUICK_DISPATCH_HISTORY_PATH", "")) or (
    Path.home() / "actions-runners" / "dashboard" / "quick_dispatch_history.json"
)

_quick_dispatch_history_lock: asyncio.Lock = asyncio.Lock()

# ─── Rate limiting (token bucket, in-process) ─────────────────────────────────

_QUICK_DISPATCH_LIMIT = 10
_QUICK_DISPATCH_WINDOW_SECONDS = 60

_quick_dispatch_timestamps: list[float] = []
_quick_dispatch_rate_lock = asyncio.Lock()


async def _check_quick_dispatch_rate() -> int | None:
    """Return None if allowed, or seconds until the window resets if rate-limited."""
    now = time.monotonic()
    async with _quick_dispatch_rate_lock:
        recent = [t for t in _quick_dispatch_timestamps if now - t < _QUICK_DISPATCH_WINDOW_SECONDS]
        if len(recent) >= _QUICK_DISPATCH_LIMIT:
            oldest = min(recent)
            retry_after = int(_QUICK_DISPATCH_WINDOW_SECONDS - (now - oldest)) + 1
            _quick_dispatch_timestamps[:] = recent
            return max(retry_after, 1)
        recent.append(now)
        _quick_dispatch_timestamps[:] = recent
        return None


# ─── Pydantic models ──────────────────────────────────────────────────────────


class QuickDispatchRequest(BaseModel):
    repository: str = Field(..., max_length=300)
    prompt: str = Field(..., max_length=10_000)
    provider: str = Field(default="claude_code_cli", max_length=100)
    model: str = Field(default="", max_length=200)
    ref: str = Field(default="main", max_length=200)
    task_kind: str = Field(default="adhoc", max_length=100)
    requested_by: str = Field(default="", max_length=200)
    principal: str = Field(default="", max_length=200)
    on_behalf_of: str = Field(default="", max_length=200)
    correlation_id: str = Field(default="", max_length=100)


class QuickDispatchResponse(BaseModel):
    accepted: bool
    envelope_id: str = ""
    fingerprint: str = ""
    workflow_run_url: str = ""
    history_id: str = ""
    reason: str = ""


# ─── Core logic ───────────────────────────────────────────────────────────────


def _build_fingerprint(repository: str, provider: str, prompt: str) -> str:
    """Short deterministic string identifying this dispatch request."""
    slug = f"{repository}|{provider}|{prompt[:80]}"
    import hashlib  # noqa: PLC0415

    return hashlib.sha256(slug.encode()).hexdigest()[:16]


async def _append_quick_dispatch_history(entry: dict[str, Any]) -> None:
    """Append an audit record to the history file (thread-safe, best-effort)."""
    async with _quick_dispatch_history_lock:
        try:
            history: list[dict[str, Any]] = []
            if _QUICK_DISPATCH_HISTORY_PATH.exists():
                try:
                    history = json.loads(_QUICK_DISPATCH_HISTORY_PATH.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    history = []
            history.append(entry)
            history = history[-200:]
            config_schema.atomic_write_json(_QUICK_DISPATCH_HISTORY_PATH, history)
        except OSError:
            log.warning("Failed to append quick-dispatch history", exc_info=True)


def _make_audit_entry(
    envelope_id: str,
    fingerprint: str,
    repository: str,
    provider: str,
    decision: str,
    detail: str,
    history_id: str,
    requested_by: str = "",
    principal: str = "",
    on_behalf_of: str = "",
    correlation_id: str = "",
    *,
    forced: bool = False,
) -> dict[str, Any]:
    return {
        "history_id": history_id,
        "envelope_id": envelope_id,
        "action": "agents.dispatch.adhoc",
        "access": DispatchAccess.PRIVILEGED.value,
        "source": "dashboard",
        "target": repository,
        "requested_by": requested_by,
        "principal": principal,
        "on_behalf_of": on_behalf_of,
        "correlation_id": correlation_id,
        "decision": decision,
        "detail": detail,
        "fingerprint": fingerprint,
        "provider": provider,
        "forced": forced,
        "recorded_at": _dt_mod.datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


async def quick_dispatch(
    req: QuickDispatchRequest,
    *,
    run_cmd_fn: Any,
    org: str,
    repo_root: Path,
    normalize_repository_fn: Any,
) -> QuickDispatchResponse:
    """Core dispatch logic extracted for testability.

    Parameters
    ----------
    req:
        Validated request model.
    run_cmd_fn:
        Async callable ``(cmd: list[str], timeout: int, cwd: Path) -> (int, str, str)``.
    org:
        GitHub organisation name (e.g. ``"D-sorganization"``).
    repo_root:
        Filesystem path to the dashboard repository root.
    normalize_repository_fn:
        Callable ``(value: str) -> (repo_name, full_repository)``.
    """
    # ── Validate prompt length ────────────────────────────────────────────────
    if len(req.prompt.strip()) < 10:
        return QuickDispatchResponse(
            accepted=False,
            reason="prompt_too_short: prompt must be at least 10 characters",
        )

    # ── Normalise repository ──────────────────────────────────────────────────
    _repo_name, full_repository = normalize_repository_fn(req.repository)

    # ── Validate provider ─────────────────────────────────────────────────────
    provider = agent_remediation.PROVIDERS.get(req.provider)
    if provider is None:
        return QuickDispatchResponse(
            accepted=False,
            reason=f"provider_unavailable: unknown provider '{req.provider}'",
        )

    availability = agent_remediation.probe_provider_availability()
    avail = availability.get(req.provider)
    if avail is None or not avail.available:
        detail = avail.detail if avail else "provider status unknown"
        return QuickDispatchResponse(
            accepted=False,
            reason=f"provider_unavailable: {detail}",
        )

    # ── Rate limit ────────────────────────────────────────────────────────────
    retry_after = await _check_quick_dispatch_rate()
    if retry_after is not None:
        return QuickDispatchResponse(
            accepted=False,
            reason=f"rate_limited: retry_after_seconds={retry_after}",
        )

    # ── Check dispatch mode ───────────────────────────────────────────────────
    if provider.dispatch_mode != "github_actions":
        return QuickDispatchResponse(
            accepted=False,
            reason=f"provider_unavailable: provider '{req.provider}' does not support github_actions dispatch",
        )

    # ── Prepare identifiers ───────────────────────────────────────────────────
    envelope_id = uuid4().hex
    fingerprint = _build_fingerprint(full_repository, req.provider, req.prompt)
    history_id = uuid4().hex

    # ── Dispatch workflow ─────────────────────────────────────────────────────
    workflow_file = "Agent-Quick-Dispatch.yml"
    endpoint = f"/repos/{org}/Repository_Management/actions/workflows/{workflow_file}/dispatches"
    payload: dict[str, Any] = {
        "ref": req.ref or "main",
        "inputs": {
            "target_repository": full_repository,
            "provider": req.provider,
            "prompt": req.prompt[:8000],
            "model": req.model or "",
            "task_kind": req.task_kind or "adhoc",
            "fingerprint": fingerprint,
            "envelope_id": envelope_id,
            "principal": req.principal or "",
            "on_behalf_of": req.on_behalf_of or "",
            "correlation_id": req.correlation_id or "",
        },
    }

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="quick-dispatch-",
        suffix=".json",
        delete=False,
    ) as payload_file:
        json.dump(payload, payload_file)
        payload_path = payload_file.name

    import contextlib  # noqa: PLC0415

    try:
        code, _stdout, stderr = await run_cmd_fn(
            ["gh", "api", endpoint, "--method", "POST", "--input", payload_path],
            timeout=30,
            cwd=repo_root,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(payload_path).unlink()

    # gh returns 422 when the workflow file does not exist
    if code != 0:
        stderr_lower = stderr.lower()
        wf_missing = "workflow" in stderr_lower and (
            "not found" in stderr_lower or "does not exist" in stderr_lower or "422" in stderr_lower
        )
        if wf_missing:
            log.warning(
                "quick-dispatch: workflow not configured repository=%s stderr=%s",
                full_repository,
                stderr.strip()[:200],
            )
            return QuickDispatchResponse(
                accepted=False,
                reason=f"workflow_not_configured: {workflow_file} does not exist in Repository_Management",
            )
        log.warning(
            "quick-dispatch: gh dispatch failed repository=%s code=%d stderr=%s",
            full_repository,
            code,
            stderr.strip()[:200],
        )
        return QuickDispatchResponse(
            accepted=False,
            reason=f"dispatch_failed: gh exited with code {code}",
        )

    # ── Record spend and lease (Wave 3) ───────────────────────────────────────
    if req.principal:
        quota_enforcement.quota_enforcement.add_spend(req.principal, 0.10)
        principal_obj = identity_manager.get_principal(req.principal)
        if principal_obj:
            from runner_lease import lease_manager  # noqa: PLC0415

            try:
                lease_manager.acquire_lease(
                    principal=principal_obj,
                    runner_id=f"virtual-{envelope_id}",
                    duration_seconds=3600,  # Default 1h lease
                    task_id=envelope_id,
                    metadata={"source": "quick_dispatch", "repo": full_repository},
                )
            except (ValueError, PermissionError) as exc:
                log.warning("Failed to acquire virtual lease for %s: %s", req.principal, exc)

    # ── Persist audit log entry ───────────────────────────────────────────────
    audit_entry = _make_audit_entry(
        envelope_id=envelope_id,
        fingerprint=fingerprint,
        repository=full_repository,
        provider=req.provider,
        decision="accepted",
        detail="quick-dispatch workflow triggered",
        history_id=history_id,
        requested_by=req.requested_by,
        principal=req.principal,
        on_behalf_of=req.on_behalf_of,
        correlation_id=req.correlation_id,
    )
    await _append_quick_dispatch_history(audit_entry)

    log.info(
        "quick-dispatch accepted envelope_id=%s repository=%s provider=%s fingerprint=%s",
        envelope_id,
        full_repository,
        req.provider,
        fingerprint,
    )

    return QuickDispatchResponse(
        accepted=True,
        envelope_id=envelope_id,
        fingerprint=fingerprint,
        workflow_run_url=f"https://github.com/{org}/Repository_Management/actions",
        history_id=history_id,
    )
