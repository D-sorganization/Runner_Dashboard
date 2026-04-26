"""Bulk PR and Issue agent dispatch logic.

Provides ``dispatch_to_prs()`` and ``dispatch_to_issues()`` that fan out
agent-dispatch workflow invocations over a selection of pull requests or
issues.  Server route handlers in server.py are thin shells that call these
functions.

Design principles
-----------------
- Reuses ``probe_provider_availability()`` from ``agent_remediation``.
- Reuses ``_normalize_repository_input()`` passed in from server.py to avoid a
  circular import.
- Uses ``asyncio.gather`` with a concurrency semaphore of 4 for parallel dispatch.
- Hard-cap of 100 targets for ``mode="all"`` to prevent runaway fan-out.
- Audit log entries are written to ``_PR_DISPATCH_HISTORY_PATH`` and
  ``_ISSUE_DISPATCH_HISTORY_PATH``.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt_mod
import json
import logging
import os
import tempfile
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

log = logging.getLogger("dashboard.agent_dispatch")

# ─── History paths ────────────────────────────────────────────────────────────

_PR_DISPATCH_HISTORY_PATH = Path(os.environ.get("PR_DISPATCH_HISTORY_PATH", "")) or (
    Path.home() / "actions-runners" / "dashboard" / "pr_dispatch_history.json"
)
_ISSUE_DISPATCH_HISTORY_PATH = Path(os.environ.get("ISSUE_DISPATCH_HISTORY_PATH", "")) or (
    Path.home() / "actions-runners" / "dashboard" / "issue_dispatch_history.json"
)

_pr_dispatch_history_lock: asyncio.Lock = asyncio.Lock()
_issue_dispatch_history_lock: asyncio.Lock = asyncio.Lock()

# ─── Constants ────────────────────────────────────────────────────────────────

DISPATCH_CONCURRENCY = 4
MAX_ALL_TARGETS = 100

# ─── Pydantic models ──────────────────────────────────────────────────────────


class DispatchItem(BaseModel):
    repository: str = Field(..., max_length=300)
    number: int


class DispatchSelection(BaseModel):
    mode: str = Field(..., pattern="^(single|repo|list|all)$")
    repository: str = Field(default="", max_length=300)
    number: int | None = None
    items: list[DispatchItem] = Field(default_factory=list)


class DispatchConfirmationBody(BaseModel):
    approved_by: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=1000)


class PRDispatchRequest(BaseModel):
    selection: DispatchSelection
    provider: str = Field(default="claude_code_cli", max_length=100)
    prompt: str = Field(default="", max_length=10_000)
    model: str = Field(default="", max_length=200)
    principal: str = Field(default="", max_length=200)
    confirmation: DispatchConfirmationBody = Field(default_factory=DispatchConfirmationBody)


class IssueDispatchRequest(BaseModel):
    selection: DispatchSelection
    provider: str = Field(default="claude_code_cli", max_length=100)
    prompt: str = Field(default="", max_length=10_000)
    model: str = Field(default="", max_length=200)
    principal: str = Field(default="", max_length=200)
    force: bool = False
    confirmation: DispatchConfirmationBody = Field(default_factory=DispatchConfirmationBody)


class RejectedTarget(BaseModel):
    repository: str
    number: int
    reason: str


class BulkDispatchResponse(BaseModel):
    accepted: int
    rejected: list[dict[str, Any]]
    envelope_ids: list[str]
    fingerprints: list[str]


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _utc_now() -> str:
    return _dt_mod.datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _build_fingerprint(kind: str, repository: str, number: int, provider: str) -> str:
    import hashlib  # noqa: PLC0415

    slug = f"{kind}|{repository}|{number}|{provider}"
    return hashlib.sha256(slug.encode()).hexdigest()[:16]


async def _append_history(
    entry: dict[str, Any],
    path: Path,
    lock: asyncio.Lock,
) -> None:
    """Append an audit record to a JSON history file (best-effort)."""
    async with lock:
        try:
            history: list[dict[str, Any]] = []
            if path.exists():
                try:
                    history = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    history = []
            history.append(entry)
            history = history[-200:]
            config_schema.atomic_write_json(path, history)
        except Exception:
            pass


def _validate_provider(provider_id: str) -> str | None:
    """Return an error reason string if the provider is unavailable, else None."""
    provider = agent_remediation.PROVIDERS.get(provider_id)
    if provider is None:
        return f"provider_unavailable: unknown provider '{provider_id}'"
    availability = agent_remediation.probe_provider_availability()
    avail = availability.get(provider_id)
    if avail is None or not avail.available:
        detail = avail.detail if avail else "provider status unknown"
        return f"provider_unavailable: {detail}"
    return None


def _resolve_targets(
    selection: DispatchSelection,
    normalize_fn: Any,
    org: str,
) -> list[tuple[str, int]] | str:
    """Resolve a DispatchSelection into a list of (full_repository, number) tuples.

    Returns an error string if resolution fails.
    """
    mode = selection.mode
    if mode == "single":
        if not selection.repository or selection.number is None:
            return "single mode requires repository and number"
        _, full = normalize_fn(selection.repository)
        return [(full, selection.number)]
    if mode == "repo":
        if not selection.repository:
            return "repo mode requires repository"
        _, full = normalize_fn(selection.repository)
        # Returns placeholder; callers that use repo mode should enumerate via API.
        # For now we return empty and let the caller handle it or error.
        return [(full, -1)]  # sentinel; caller must pre-resolve
    if mode == "list":
        if not selection.items:
            return "list mode requires at least one item"
        result: list[tuple[str, int]] = []
        for item in selection.items:
            _, full = normalize_fn(item.repository)
            result.append((full, item.number))
        return result
    if mode == "all":
        # all mode: items must be pre-populated by the caller
        if not selection.items:
            return "all mode requires pre-populated items list"
        if len(selection.items) > MAX_ALL_TARGETS:
            return f"all mode hard-cap exceeded: {len(selection.items)} > {MAX_ALL_TARGETS}"
        result = []
        for item in selection.items:
            _, full = normalize_fn(item.repository)
            result.append((full, item.number))
        return result
    return f"unknown selection mode: {mode}"


async def _dispatch_one(
    *,
    kind: str,
    full_repository: str,
    number: int,
    provider: str,
    prompt: str,
    model: str,
    workflow_file: str,
    org: str,
    repo_root: Path,
    run_cmd_fn: Any,
    semaphore: asyncio.Semaphore,
    extra_inputs: dict[str, str] | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Dispatch a single PR/issue.  Returns (envelope_id, fingerprint, error_reason)."""
    async with semaphore:
        envelope_id = uuid4().hex
        fingerprint = _build_fingerprint(kind, full_repository, number, provider)
        endpoint = f"/repos/{org}/Repository_Management/actions/workflows/{workflow_file}/dispatches"
        inputs: dict[str, str] = {
            "target_repository": full_repository,
            "number": str(number),
            "provider": provider,
            "prompt": prompt[:8000],
            "model": model or "",
            "fingerprint": fingerprint,
            "envelope_id": envelope_id,
        }
        if extra_inputs:
            inputs.update(extra_inputs)
        payload: dict[str, Any] = {"ref": "main", "inputs": inputs}

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f"agent-dispatch-{kind}-",
            suffix=".json",
            delete=False,
        ) as f:
            json.dump(payload, f)
            payload_path = f.name

        try:
            code, _stdout, stderr = await run_cmd_fn(
                ["gh", "api", endpoint, "--method", "POST", "--input", payload_path],
                timeout=30,
                cwd=repo_root,
            )
        finally:
            with contextlib.suppress(OSError):
                Path(payload_path).unlink()

        if code != 0:
            stderr_lower = stderr.lower()
            if "workflow" in stderr_lower and (
                "not found" in stderr_lower or "does not exist" in stderr_lower or "422" in stderr_lower
            ):
                reason = f"workflow_not_configured: {workflow_file} does not exist in Repository_Management"
            else:
                reason = f"dispatch_failed: gh exited with code {code}"
            log.warning(
                "agent-dispatch %s failed repository=%s number=%d reason=%s",
                kind,
                full_repository,
                number,
                reason,
            )
            return None, None, reason

        log.info(
            "agent-dispatch %s accepted envelope_id=%s repository=%s number=%d",
            kind,
            envelope_id,
            full_repository,
            number,
        )
        return envelope_id, fingerprint, None


# ─── Public API ───────────────────────────────────────────────────────────────


async def dispatch_to_prs(
    req: PRDispatchRequest,
    *,
    run_cmd_fn: Any,
    org: str,
    repo_root: Path,
    normalize_repository_fn: Any,
) -> BulkDispatchResponse | dict[str, Any]:
    """Dispatch agents to one or more pull requests.

    Returns a ``BulkDispatchResponse`` on success/partial success, or a plain
    dict with ``{"error": ..., "status_code": N}`` for hard failures.
    """
    # ── Validate provider ─────────────────────────────────────────────────────
    provider_error = _validate_provider(req.provider)
    if provider_error:
        return {"error": provider_error, "status_code": 409}

    # ── Resolve targets ───────────────────────────────────────────────────────
    targets_or_err = _resolve_targets(req.selection, normalize_repository_fn, org)
    if isinstance(targets_or_err, str):
        if "hard-cap" in targets_or_err:
            return {"error": targets_or_err, "status_code": 400}
        return {"error": targets_or_err, "status_code": 422}
    targets = targets_or_err

    # Wave 3: Quota truncation (Fair Sharing)
    rejected_due_to_quota: list[tuple[str, int]] = []
    if req.principal:
        principal_obj = identity_manager.get_principal(req.principal)
        if principal_obj:
            from runner_lease import lease_manager  # noqa: PLC0415

            active_leases = lease_manager.get_active_leases(principal_obj.id)
            remaining = max(0, principal_obj.quotas.max_runners - len(active_leases))
            if len(targets) > remaining:
                rejected_due_to_quota = targets[remaining:]
                targets = targets[:remaining]
                log.info(
                    "Principal %s bulk PR dispatch truncated: %d targets accepted, %d rejected due to quota",
                    req.principal,
                    len(targets),
                    len(rejected_due_to_quota),
                )

    # ── Fan-out dispatch ──────────────────────────────────────────────────────
    semaphore = asyncio.Semaphore(DISPATCH_CONCURRENCY)
    tasks = [
        _dispatch_one(
            kind="pr",
            full_repository=repo,
            number=num,
            provider=req.provider,
            prompt=req.prompt,
            model=req.model,
            workflow_file="Agent-PR-Action.yml",
            org=org,
            repo_root=repo_root,
            run_cmd_fn=run_cmd_fn,
            semaphore=semaphore,
        )
        for repo, num in targets
    ]
    results = await asyncio.gather(*tasks)

    # ── Collate ───────────────────────────────────────────────────────────────
    envelope_ids: list[str] = []
    fingerprints: list[str] = []
    rejected: list[dict[str, Any]] = []
    accepted_count = 0

    for (repo, num), (env_id, fp, reason) in zip(targets, results, strict=True):
        if reason:
            rejected.append({"repository": repo, "number": num, "reason": reason})
        else:
            accepted_count += 1
            if env_id:
                envelope_ids.append(env_id)
            if fp:
                fingerprints.append(fp)
            # Wave 3: Acquire virtual lease
            if req.principal and env_id:
                principal_obj = identity_manager.get_principal(req.principal)
                if principal_obj:
                    from runner_lease import lease_manager  # noqa: PLC0415

                    try:
                        lease_manager.acquire_lease(
                            principal=principal_obj,
                            runner_id=f"virtual-{env_id}",
                            duration_seconds=3600,
                            task_id=env_id,
                            metadata={"source": "agent_dispatch_router", "repo": repo, "number": num},
                        )
                    except (ValueError, PermissionError) as exc:
                        log.warning("Failed to acquire virtual lease for %s: %s", req.principal, exc)

    # Handle quota rejected targets
    for repo, num in rejected_due_to_quota:
        rejected.append({"repository": repo, "number": num, "reason": "quota_exceeded: max_runners reached"})

    # ── Audit log ─────────────────────────────────────────────────────────────
    audit_entry: dict[str, Any] = {
        "history_id": uuid4().hex,
        "action": "agents.dispatch.pr",
        "access": DispatchAccess.PRIVILEGED.value,
        "provider": req.provider,
        "accepted": accepted_count,
        "rejected_count": len(rejected),
        "envelope_ids": envelope_ids,
        "fingerprints": fingerprints,
        "recorded_at": _utc_now(),
    }
    # ── Record spend (Wave 3) ─────────────────────────────────────────────────
    if req.principal and accepted_count > 0:
        quota_enforcement.quota_enforcement.add_spend(req.principal, accepted_count * 0.10)

    await _append_history(audit_entry, _PR_DISPATCH_HISTORY_PATH, _pr_dispatch_history_lock)

    return BulkDispatchResponse(
        accepted=accepted_count,
        rejected=rejected,
        envelope_ids=envelope_ids,
        fingerprints=fingerprints,
    )


def _check_issue_pickable(repository: str, number: int) -> str | None:
    """Return a non-pickable reason string, or None if the issue is pickable.

    This is a lightweight heuristic check.  Full pickability is enforced by
    the issue taxonomy rules; here we only block obviously invalid numbers.
    """
    if number <= 0:
        return "invalid issue number"
    return None  # default: assume pickable (no live API call here)


async def dispatch_to_issues(
    req: IssueDispatchRequest,
    *,
    run_cmd_fn: Any,
    org: str,
    repo_root: Path,
    normalize_repository_fn: Any,
) -> BulkDispatchResponse | dict[str, Any]:
    """Dispatch agents to one or more issues.

    Parameters
    ----------
    req:
        Validated request model.  If ``req.force`` is True and the caller has
        already verified PRIVILEGED access, pickability checks are skipped.
    run_cmd_fn:
        Async callable ``(cmd: list[str], timeout: int, cwd: Path) -> (int, str, str)``.
    org:
        GitHub organisation name.
    repo_root:
        Filesystem path to the dashboard repository root.
    normalize_repository_fn:
        Callable ``(value: str) -> (repo_name, full_repository)``.
    """
    # ── Validate provider ─────────────────────────────────────────────────────
    provider_error = _validate_provider(req.provider)
    if provider_error:
        return {"error": provider_error, "status_code": 409}

    # ── Resolve targets ───────────────────────────────────────────────────────
    targets_or_err = _resolve_targets(req.selection, normalize_repository_fn, org)
    if isinstance(targets_or_err, str):
        if "hard-cap" in targets_or_err:
            return {"error": targets_or_err, "status_code": 400}
        return {"error": targets_or_err, "status_code": 422}
    targets = targets_or_err

    # Wave 3: Quota truncation (Fair Sharing)
    rejected_due_to_quota: list[tuple[str, int]] = []
    if req.principal:
        principal_obj = identity_manager.get_principal(req.principal)
        if principal_obj:
            from runner_lease import lease_manager  # noqa: PLC0415

            active_leases = lease_manager.get_active_leases(principal_obj.id)
            remaining = max(0, principal_obj.quotas.max_runners - len(active_leases))
            if len(targets) > remaining:
                rejected_due_to_quota = targets[remaining:]
                targets = targets[:remaining]
                log.info(
                    "Principal %s bulk issue dispatch truncated: %d targets accepted, %d rejected due to quota",
                    req.principal,
                    len(targets),
                    len(rejected_due_to_quota),
                )

    # ── Pickability pre-filter ────────────────────────────────────────────────
    pre_rejected: list[dict[str, Any]] = []
    filtered_targets: list[tuple[str, int]] = []
    for repo, num in targets:
        if not req.force:
            not_pickable = _check_issue_pickable(repo, num)
            if not_pickable:
                pre_rejected.append(
                    {
                        "repository": repo,
                        "number": num,
                        "reason": f"not_pickable: {not_pickable}",
                    }
                )
                continue
        filtered_targets.append((repo, num))

    # ── Fan-out dispatch ──────────────────────────────────────────────────────
    semaphore = asyncio.Semaphore(DISPATCH_CONCURRENCY)
    extra = {"forced": "true"} if req.force else {}
    tasks = [
        _dispatch_one(
            kind="issue",
            full_repository=repo,
            number=num,
            provider=req.provider,
            prompt=req.prompt,
            model=req.model,
            workflow_file="Agent-Issue-Action.yml",
            org=org,
            repo_root=repo_root,
            run_cmd_fn=run_cmd_fn,
            semaphore=semaphore,
            extra_inputs=extra,
        )
        for repo, num in filtered_targets
    ]
    results = await asyncio.gather(*tasks)

    # ── Collate ───────────────────────────────────────────────────────────────
    envelope_ids: list[str] = []
    fingerprints: list[str] = []
    rejected: list[dict[str, Any]] = list(pre_rejected)
    accepted_count = 0

    for (repo, num), (env_id, fp, reason) in zip(filtered_targets, results, strict=True):
        if reason:
            rejected.append({"repository": repo, "number": num, "reason": reason})
        else:
            accepted_count += 1
            if env_id:
                envelope_ids.append(env_id)
            if fp:
                fingerprints.append(fp)
            # Wave 3: Acquire virtual lease
            if req.principal and env_id:
                principal_obj = identity_manager.get_principal(req.principal)
                if principal_obj:
                    from runner_lease import lease_manager  # noqa: PLC0415

                    try:
                        lease_manager.acquire_lease(
                            principal=principal_obj,
                            runner_id=f"virtual-{env_id}",
                            duration_seconds=3600,
                            task_id=env_id,
                            metadata={"source": "agent_dispatch_router", "repo": repo, "number": num},
                        )
                    except (ValueError, PermissionError) as exc:
                        log.warning("Failed to acquire virtual lease for %s: %s", req.principal, exc)

    # Handle quota rejected targets
    for repo, num in rejected_due_to_quota:
        rejected.append({"repository": repo, "number": num, "reason": "quota_exceeded: max_runners reached"})

    # ── Audit log ─────────────────────────────────────────────────────────────
    audit_entry: dict[str, Any] = {
        "history_id": uuid4().hex,
        "action": "agents.dispatch.issue",
        "access": DispatchAccess.PRIVILEGED.value,
        "provider": req.provider,
        "accepted": accepted_count,
        "rejected_count": len(rejected),
        "envelope_ids": envelope_ids,
        "fingerprints": fingerprints,
        "forced": req.force,
        "recorded_at": _utc_now(),
    }
    # ── Record spend (Wave 3) ─────────────────────────────────────────────────
    if req.principal and accepted_count > 0:
        quota_enforcement.quota_enforcement.add_spend(req.principal, accepted_count * 0.10)

    await _append_history(audit_entry, _ISSUE_DISPATCH_HISTORY_PATH, _issue_dispatch_history_lock)

    return BulkDispatchResponse(
        accepted=accepted_count,
        rejected=rejected,
        envelope_ids=envelope_ids,
        fingerprints=fingerprints,
    )
