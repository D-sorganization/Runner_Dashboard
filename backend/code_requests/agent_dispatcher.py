"""Agent-agnostic Code Request dispatch using CommandEnvelopes (CR-3, issue #1283)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import agent_remediation
import config_schema
import dispatch_contract
import dispatch_quota
import quota_enforcement
from identity import identity_manager
from system_utils import run_cmd

log = logging.getLogger("dashboard.code_requests.agent_dispatcher")

_AUDIT_HISTORY_PATH = Path.home() / "actions-runners" / "dashboard" / "agent_dispatch_history.json"
_audit_lock = asyncio.Lock()


def validate_provider(provider_id: str) -> str | None:
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


async def append_audit_entry(entry: dict[str, Any], path: Path = _AUDIT_HISTORY_PATH) -> None:
    """Append an audit record to a JSON history file (best-effort)."""
    async with _audit_lock:
        try:
            history: list[dict[str, Any]] = []
            if path.exists():
                try:
                    history = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    history = []
            history.append(entry)
            history = history[-200:]
            config_schema.atomic_write_json(path, history)
        except OSError:
            log.warning("Failed to append dispatch history to %s", path, exc_info=True)


async def dispatch_code_request(
    *,
    repository: str,
    branch: str,
    provider: str,
    prompt: str,
    model: str = "",
    effort: str | None = None,
    principal: str = "",
    budget: dict[str, Any] | None = None,
    profile_id: str | None = None,
    standards: list[str] | None = None,
    run_cmd_fn: Any = None,
    org: str = "D-sorganization",
    repo_root: Path | None = None,
) -> tuple[int, str, Any]:
    """Dispatch a Code Request task using the command envelope (CR-3, #1283).

    Returns (status_code, error_or_stderr, CommandEnvelope).
    """
    if principal and dispatch_quota.quota.is_anonymous(principal):
        return 422, "anonymous_principal: dispatch requires an authenticated principal", None

    if principal:
        quota_check = await dispatch_quota.quota.check_and_record(principal)
        if not quota_check["allowed"]:
            return 429, f"rate_limited: {quota_check['reason']}", None

    # Budget overrun check
    max_cost = float((budget or {}).get("max_cost", 0.0))
    if principal and max_cost > 0:
        principal_obj = identity_manager.get_principal(principal)
        if principal_obj:
            allowed, reason = quota_enforcement.quota_enforcement.check_dispatch_quota(
                principal_obj, estimated_cost=max_cost
            )
            if not allowed:
                return 422, f"budget_overrun: {reason}", None

    provider_err = validate_provider(provider)
    if provider_err:
        log.warning("code request provider unavailable: %s", provider_err)
        return 409, provider_err, None

    confirmation = dispatch_contract.DispatchConfirmation(
        approved_by=principal or "operator",
        approved_at=datetime.now(UTC).isoformat(),
        note="Code Request dispatch",
    )
    envelope = dispatch_contract.build_envelope(
        action="agents.dispatch.adhoc",
        source="dashboard.code_requests",
        target=repository,
        requested_by=principal or "operator",
        principal=principal or "operator",
        confirmation=confirmation,
        payload={
            "target_repository": f"{org}/{repository}" if "/" not in repository else repository,
            "branch": branch,
            "provider": provider,
            "prompt": prompt[:8000],
            "model": model or "",
            "effort": effort or "",
            "profile_id": profile_id or "",
            "standards": list(standards or []),
            "budget": budget or {},
        },
    )
    val_res = dispatch_contract.validate_envelope(envelope)
    if not val_res.accepted:
        return 422, f"invalid_envelope: {val_res.reason}", envelope

    if run_cmd_fn is None:
        run_cmd_fn = run_cmd

    payload_data = {"ref": "main", "inputs": envelope.payload}
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="agent-dispatch-cr-", suffix=".json", delete=False
    ) as f:
        json.dump(payload_data, f)
        payload_path = f.name

    try:
        target_endpoint = f"/repos/{org}/Repository_Management/actions/workflows/Agent-Quick-Dispatch.yml/dispatches"
        code, _, stderr = await run_cmd_fn(
            ["gh", "api", target_endpoint, "--method", "POST", "--input", payload_path],
            timeout=30,
            cwd=repo_root or Path.cwd(),
        )
    finally:
        with contextlib.suppress(OSError):
            Path(payload_path).unlink()

    audit_entry: dict[str, Any] = {
        "history_id": uuid4().hex,
        "action": "agents.dispatch.adhoc",
        "access": "privileged",
        "provider": provider,
        "accepted": 1 if code == 0 else 0,
        "rejected_count": 0 if code == 0 else 1,
        "envelope_ids": [envelope.envelope_id],
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    await append_audit_entry(audit_entry)
    return code, stderr, envelope
