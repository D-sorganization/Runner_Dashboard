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
from cache_utils import cache_get, cache_set
from dispatch_contract import DispatchAccess
from gh_utils import gh_api_admin
from identity import identity_manager
from pydantic import BaseModel, Field
from readiness import aggregate, get_default_probes
from runner_inventory import fetch_org_runners

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017

log = logging.getLogger("dashboard.quick_dispatch")

# ─── History path ─────────────────────────────────────────────────────────────

_QUICK_DISPATCH_HISTORY_PATH = Path(
    os.environ.get("QUICK_DISPATCH_HISTORY_PATH")
    or (Path.home() / "actions-runners" / "dashboard" / "quick_dispatch_history.json")
)

_quick_dispatch_history_lock: asyncio.Lock = asyncio.Lock()

# ─── Rate limiting (token bucket, in-process) ─────────────────────────────────

_QUICK_DISPATCH_LIMIT = 10
_QUICK_DISPATCH_WINDOW_SECONDS = 60

_quick_dispatch_timestamps: list[float] = []
_quick_dispatch_rate_lock = asyncio.Lock()

_QUICK_DISPATCH_HEALTH_CACHE_SECONDS = 5.0
_QUICK_DISPATCH_HEALTH_RETRY_AFTER_SECONDS = 30
_QUICK_DISPATCH_REQUIRED_LABELS = ("d-sorg-fleet",)

_quick_dispatch_health_lock = asyncio.Lock()
_quick_dispatch_health_cache: dict[
    tuple[str, tuple[str, ...]],
    tuple[float, QuickDispatchHealthGateResult],
] = {}


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


# ─── Health gate ──────────────────────────────────────────────────────────────


class HealthGate:
    """Cache-backed readiness gate for dispatch pre-flight (issue #709).

    Checks the /readyz aggregate probe before allowing dispatch.
    Results are cached for ``cache_ttl`` seconds to avoid hammering the probe.
    Fails open if the probe itself raises an exception.
    """

    _cache_ttl: float
    _last_check: float
    _last_ok: bool
    _lock: asyncio.Lock

    def __init__(self, cache_ttl: float = 5.0) -> None:
        assert cache_ttl > 0, "cache_ttl must be positive"
        self._cache_ttl = cache_ttl
        self._last_check = 0.0
        self._last_ok = True
        self._lock = asyncio.Lock()

    async def is_ready(self) -> tuple[bool, str]:
        """Return (ready, reason). Cached for cache_ttl seconds.

        Pre-condition: cache_ttl > 0 (enforced in __init__).
        Post-condition: always returns a (bool, str) tuple.
        """
        now = time.monotonic()
        async with self._lock:
            if now - self._last_check < self._cache_ttl:
                return self._last_ok, ""
            # Do actual check
            try:
                from readiness import aggregate, get_default_probes  # noqa: PLC0415

                http_status, _ = await aggregate(get_default_probes())
                self._last_ok = http_status == 200
                self._last_check = now
                return self._last_ok, "" if self._last_ok else "readyz_failed"
            except Exception:  # noqa: BLE001
                # Fail open: if the check itself errors, don't block dispatch
                self._last_check = now
                return True, ""


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
    force: bool = Field(default=False, description="Bypass health gate and runner checks (audit-logged)")


class QuickDispatchResponse(BaseModel):
    accepted: bool
    envelope_id: str = ""
    fingerprint: str = ""
    workflow_run_url: str = ""
    history_id: str = ""
    reason: str = ""
    error_code: str = ""
    retry_after_seconds: int = 0


class QuickDispatchHealthGateResult(BaseModel):
    ready: bool
    reason: str = ""
    retry_after_seconds: int = _QUICK_DISPATCH_HEALTH_RETRY_AFTER_SECONDS


# ─── Core logic ───────────────────────────────────────────────────────────────


def _build_fingerprint(repository: str, provider: str, prompt: str) -> str:
    """Short deterministic string identifying this dispatch request."""
    slug = f"{repository}|{provider}|{prompt[:80]}"
    import hashlib  # noqa: PLC0415

    return hashlib.sha256(slug.encode()).hexdigest()[:16]


def _runner_label_names(runner: dict[str, Any]) -> set[str]:
    labels = runner.get("labels", [])
    result: set[str] = set()
    for label in labels:
        if isinstance(label, dict):
            name = str(label.get("name", "")).strip().lower()
        else:
            name = str(label).strip().lower()
        if name:
            result.add(name)
    return result


def _has_online_runner_with_labels(
    runners: list[dict[str, Any]],
    required_labels: tuple[str, ...],
) -> bool:
    expected = {label.strip().lower() for label in required_labels if label.strip()}
    if not expected:
        return True
    for runner in runners:
        if str(runner.get("status", "")).lower() != "online":
            continue
        labels = _runner_label_names(runner)
        if expected.issubset(labels):
            return True
    return False


async def _probe_quick_dispatch_health(
    *,
    org: str,
    required_labels: tuple[str, ...],
) -> QuickDispatchHealthGateResult:
    http_status, _body = await aggregate(get_default_probes())
    if http_status != 200:
        return QuickDispatchHealthGateResult(
            ready=False,
            reason="readyz_failed",
            retry_after_seconds=_QUICK_DISPATCH_HEALTH_RETRY_AFTER_SECONDS,
        )

    runners_data = cache_get("runners", _QUICK_DISPATCH_HEALTH_CACHE_SECONDS)
    if runners_data is None:
        runners_data = await fetch_org_runners(gh_api_admin, org)
        cache_set("runners", runners_data)

    runners = list(runners_data.get("runners", []) or [])
    if not _has_online_runner_with_labels(runners, required_labels):
        return QuickDispatchHealthGateResult(
            ready=False,
            reason="no_online_runners",
            retry_after_seconds=_QUICK_DISPATCH_HEALTH_RETRY_AFTER_SECONDS,
        )

    return QuickDispatchHealthGateResult(ready=True)


async def _get_cached_quick_dispatch_health(
    *,
    org: str,
    required_labels: tuple[str, ...],
    health_gate_fn: Any,
) -> QuickDispatchHealthGateResult:
    now = time.monotonic()
    cache_key = (org, required_labels)
    cached = _quick_dispatch_health_cache.get(cache_key)
    if cached is not None and now - cached[0] < _QUICK_DISPATCH_HEALTH_CACHE_SECONDS:
        return cached[1]

    async with _quick_dispatch_health_lock:
        cached = _quick_dispatch_health_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < _QUICK_DISPATCH_HEALTH_CACHE_SECONDS:
            return cached[1]

        result = await health_gate_fn(org=org, required_labels=required_labels)
        _quick_dispatch_health_cache[cache_key] = (now, result)
        return result


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
    health_gate_fn: Any | None = None,
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

    if req.force:
        log.warning(
            "dispatch.force_override requested_by=%s principal=%s repository=%s",
            req.requested_by or "unknown",
            req.principal or "unknown",
            full_repository,
        )
    else:
        health_result = await _get_cached_quick_dispatch_health(
            org=org,
            required_labels=_QUICK_DISPATCH_REQUIRED_LABELS,
            health_gate_fn=health_gate_fn or _probe_quick_dispatch_health,
        )
        if not health_result.ready:
            return QuickDispatchResponse(
                accepted=False,
                reason=health_result.reason,
                error_code="not_ready",
                retry_after_seconds=health_result.retry_after_seconds,
            )

    # ── Rate limit ────────────────────────────────────────────────────────────
    retry_after = await _check_quick_dispatch_rate()
    if retry_after is not None:
        return QuickDispatchResponse(
            accepted=False,
            reason=f"rate_limited: retry_after_seconds={retry_after}",
            error_code="rate_limited",
            retry_after_seconds=retry_after,
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
        forced=req.force,
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
