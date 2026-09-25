"""Action proposals registry, approval policies, execution, and verification (SC-B6, Issue #1313).

Provides:
- ActionRegistry and standard action catalogue
- Risk-based approval policy checks (read/low auto-execute, medium user approve, high/owner-only owner approve)
- Replay and 24-hour expiry protection
- Role permission gates (proposing role must be authorized)
- Post-execution verification and audit logging (SC-A8, SC-B7)
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from identity import Principal, format_caller
from staff.action_executors import (
    execute_board_propose,
    execute_code_request_create,
    execute_maintenance_action,
    execute_review_pr,
    execute_staff_dispatch,
    execute_staff_hold,
    execute_staff_unhold,
    verify_staff_dispatch,
    verify_staff_hold,
    verify_staff_unhold,
)
from staff.conversation_models import ActionProposalRecord
from staff.conversations import get_conversation_store
from staff.roles import RoleSpec, load_roles

if TYPE_CHECKING:
    from staff.audit import StaffAuditStore
    from staff.conversations import ConversationStore

UTC = getattr(_dt, "UTC", _dt.UTC)
datetime = _dt.datetime
log = logging.getLogger("dashboard.staff.actions")

PROPOSAL_TTL_SECONDS = 86400  # 24 hours


class ActionRiskClass:
    READ = "read"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    OWNER_ONLY = "owner-only"


class ActionError(Exception):
    """Base error for action operations."""


class ProposalExpiredError(ActionError):
    """Raised when an action proposal has expired (age > 24 hours)."""


class ProposalReplayError(ActionError):
    """Raised when an action proposal cannot be executed due to terminal/invalid state."""


class RolePermissionDeniedError(ActionError):
    """Raised when a proposing role is not permitted to trigger the action."""


@dataclass
class ActionResult:
    """Outcome of an action execution."""

    success: bool
    result: Any = None
    run_id: str | None = None
    error: str | None = None
    failure_class: str | None = None
    verification_ok: bool = True
    verification_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "result": self.result,
            "run_id": self.run_id,
            "error": self.error,
            "failure_class": self.failure_class,
            "verification_ok": self.verification_ok,
            "verification_message": self.verification_message,
        }


@dataclass
class ActionContext:
    """Execution context provided to action executors and verifiers."""

    thread_id: str = ""
    message_id: str = ""
    proposal_id: str = ""
    proposing_role: str = ""
    caller: Principal | None = None
    store: ConversationStore | None = None
    audit_store: StaffAuditStore | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class ActionDefinition:
    """Allowlisted action specification."""

    name: str
    description: str
    params_schema: dict[str, Any] = field(default_factory=dict)
    required_scope: str = "staff.dispatch"
    risk_class: str = ActionRiskClass.MEDIUM
    executor: Callable[[dict[str, Any], ActionContext], ActionResult] = field(
        default_factory=lambda: lambda p, c: ActionResult(success=True)
    )
    verifier: Callable[[ActionResult, dict[str, Any], ActionContext], tuple[bool, str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "params_schema": dict(self.params_schema),
            "required_scope": self.required_scope,
            "risk_class": self.risk_class,
        }


class ActionRegistry:
    """Thread-safe registry of allowlisted staff conversation actions."""

    def __init__(self) -> None:
        self._actions: dict[str, ActionDefinition] = {}

    def register(self, action: ActionDefinition) -> None:
        self._actions[action.name] = action

    def get(self, name: str) -> ActionDefinition | None:
        return self._actions.get(name)

    def list_actions(self) -> list[ActionDefinition]:
        return sorted(self._actions.values(), key=lambda a: a.name)


ACTION_REGISTRY = ActionRegistry()


def is_proposal_expired(prop: ActionProposalRecord, ttl_seconds: int = PROPOSAL_TTL_SECONDS) -> bool:
    """Return True if the proposal is older than ttl_seconds."""
    try:
        created = datetime.fromisoformat(prop.created_at.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - created).total_seconds()
        return age > ttl_seconds
    except Exception:
        return False


def is_owner(principal: Principal | None) -> bool:
    """Return True if principal has owner/admin privileges."""
    if not principal:
        return False
    roles = {r.lower() for r in (principal.roles or [])}
    scopes = {s.lower() for s in (principal.scopes or [])}
    if "owner" in roles or "admin" in roles:
        return True
    if "owner" in scopes or "admin" in scopes:
        return True
    if principal.id.lower() in ("owner", "operator") or principal.name.lower() in ("owner", "operator"):
        return True
    return False


def can_auto_execute(action: ActionDefinition, role_name: str, caller: Principal | None) -> bool:
    """Check if action can auto-execute without human approval (read/low only)."""
    return action.risk_class in (ActionRiskClass.READ, ActionRiskClass.LOW)


def check_approval_policy(
    action: ActionDefinition | None,
    prop: ActionProposalRecord,
    approver: Principal,
) -> None:
    """Enforce risk-based approval policy gates."""
    if is_proposal_expired(prop):
        raise ProposalExpiredError(f"Proposal {prop.id} has expired (exceeded 24h TTL)")

    risk = action.risk_class if action else prop.risk
    if risk in (ActionRiskClass.HIGH, ActionRiskClass.CRITICAL, ActionRiskClass.OWNER_ONLY):
        if not is_owner(approver):
            raise PermissionError(f"Action '{prop.action}' has risk '{risk}' and requires owner approval")

    scopes = set(approver.scopes or [])
    if "staff.approve" not in scopes and not is_owner(approver):
        raise PermissionError(f"Principal '{approver.id}' lacks 'staff.approve' scope")


def check_role_permission(
    action: ActionDefinition | None,
    role_name: str,
    role_spec: RoleSpec | None = None,
) -> bool:
    """Return True if role_name is authorized to propose/trigger the action."""
    if not role_name or role_name.lower() in ("barb", "user", "operator", "owner"):
        return True

    act_name = action.name if action else ""
    spec = role_spec or load_roles().get(role_name)
    if not spec:
        return False

    perms = spec.permissions or {}
    allowed = set(perms.get("allowed_actions") or perms.get("actions") or [])
    fleet_acts = set(spec.fleet_actions)

    if "*" in allowed or "*" in fleet_acts or act_name in allowed or act_name in fleet_acts:
        return True

    for pat in allowed | fleet_acts:
        if pat.endswith(".*") and act_name.startswith(pat[:-2] + "."):
            return True

    if act_name == "staff.dispatch" and perms.get("can_dispatch", True) and not allowed:
        return True

    return False


def execute_proposal(
    proposal_id: str,
    approver: Principal,
    store: ConversationStore | None = None,
    audit_store: StaffAuditStore | None = None,
    auto_execute: bool = False,
) -> ActionResult:
    """Approve and execute a proposal with policy checks, verifier, and audit trails."""
    s = store or get_conversation_store()
    prop = s.get_proposal(proposal_id)
    if not prop:
        raise ProposalReplayError(f"Proposal {proposal_id} not found")

    if is_proposal_expired(prop):
        if prop.state == "proposed":
            s.transition_proposal_state(proposal_id, "expired", reason="Expired after 24h", audit_store=audit_store)
        raise ProposalExpiredError(f"Proposal {proposal_id} has expired (exceeded 24h TTL)")

    if prop.state in ("denied", "expired", "done"):
        raise ProposalReplayError(f"Proposal {proposal_id} is in terminal state '{prop.state}' and cannot be executed")

    action_def = ACTION_REGISTRY.get(prop.action)
    if not action_def:
        s.transition_proposal_state(
            proposal_id, "failed", reason=f"Unknown action '{prop.action}'", audit_store=audit_store
        )
        return ActionResult(
            success=False, error=f"Action '{prop.action}' not registered", failure_class="unknown_action"
        )

    proposing_role = str(prop.params.get("proposing_role") or prop.params.get("role") or "")
    if not proposing_role and prop.message_id:
        msg = s.get_message(prop.message_id)
        if msg and msg.author_kind == "role":
            proposing_role = msg.author
    if proposing_role and not check_role_permission(action_def, proposing_role):
        s.transition_proposal_state(
            proposal_id,
            "failed",
            reason=f"Role '{proposing_role}' not permitted for action '{prop.action}'",
            audit_store=audit_store,
        )
        raise RolePermissionDeniedError(f"Role '{proposing_role}' is not permitted to invoke '{prop.action}'")

    if not auto_execute:
        check_approval_policy(action_def, prop, approver)

    s.transition_proposal_state(proposal_id, "executing", decided_by=format_caller(approver), audit_store=audit_store)

    ctx = ActionContext(
        thread_id=prop.thread_id,
        message_id=prop.message_id,
        proposal_id=prop.id,
        proposing_role=proposing_role,
        caller=approver,
        store=s,
        audit_store=audit_store,
    )

    try:
        res = action_def.executor(prop.params, ctx)
    except Exception as exc:  # noqa: BLE001
        log.exception("Action execution raised exception: %s", exc)
        res = ActionResult(success=False, error=str(exc), failure_class="execution_exception")

    if res.success and action_def.verifier:
        try:
            ok, vmsg = action_def.verifier(res, prop.params, ctx)
            res.verification_ok = ok
            res.verification_message = vmsg
            if not ok:
                res.success = False
                res.error = f"Verification mismatch: {vmsg}"
                res.failure_class = "verification_mismatch"
        except Exception as vexc:  # noqa: BLE001
            res.verification_ok = False
            res.success = False
            res.error = f"Verification error: {vexc}"
            res.failure_class = "verification_error"

    if res.success:
        s.transition_proposal_state(proposal_id, "done", audit_store=audit_store)
        if prop.thread_id:
            formatted_res = json.dumps(res.result) if isinstance(res.result, dict) else str(res.result)
            s.add_message(
                thread_id=prop.thread_id,
                author_kind="system",
                author="system",
                kind="action_result",
                body_md=f"**Action Executed**: `{prop.action}`\n\nResult: {formatted_res}",
                meta={"proposal_id": prop.id, "action": prop.action, "success": True, "result": res.result},
            )
            if res.run_id:
                s.add_message(
                    thread_id=prop.thread_id,
                    author_kind="system",
                    author="system",
                    kind="run_card",
                    run_id=res.run_id,
                    body_md=f"**Staff Run Started**: `{res.run_id}`",
                    meta={"run_id": res.run_id, "action": prop.action},
                )
    else:
        s.transition_proposal_state(
            proposal_id,
            "failed",
            reason=res.error or "Action failed",
            audit_store=audit_store,
        )
        if prop.thread_id:
            s.add_message(
                thread_id=prop.thread_id,
                author_kind="system",
                author="system",
                kind="action_result",
                body_md=f"**Action Failed**: `{prop.action}`\n\nError: {res.error}\n\n*You can retry this action.*",
                meta={
                    "proposal_id": prop.id,
                    "action": prop.action,
                    "success": False,
                    "error": res.error,
                    "failure_class": res.failure_class,
                    "can_retry": True,
                },
            )

    return res


# ─── Register Standard Catalogue ─────────────────────────────────────────────

ACTION_REGISTRY.register(
    ActionDefinition(
        name="staff.dispatch",
        description="Dispatch an AI staff role to work on an issue, PR, or prompt.",
        params_schema={"role": "string", "repo": "string?", "prompt": "string?", "issue": "int?", "pr": "int?"},
        required_scope="staff.dispatch",
        risk_class=ActionRiskClass.MEDIUM,
        executor=execute_staff_dispatch,
        verifier=verify_staff_dispatch,
    )
)

ACTION_REGISTRY.register(
    ActionDefinition(
        name="staff.review_pr",
        description="Request a PR review from a specialist staff role.",
        params_schema={"repo": "string", "pr": "int", "reviewer": "string?", "focus": "string?"},
        required_scope="staff.dispatch",
        risk_class=ActionRiskClass.LOW,
        executor=execute_review_pr,
        verifier=verify_staff_dispatch,
    )
)

ACTION_REGISTRY.register(
    ActionDefinition(
        name="staff.hold",
        description="Set an operational hold locking a role or policy.",
        params_schema={"text": "string", "applies_to": "list[string]?", "lifted_when": "string?"},
        required_scope="staff.holds.write",
        risk_class=ActionRiskClass.HIGH,
        executor=execute_staff_hold,
        verifier=verify_staff_hold,
    )
)

ACTION_REGISTRY.register(
    ActionDefinition(
        name="staff.unhold",
        description="Lift an operational hold.",
        params_schema={"hold_id": "string?", "text": "string?"},
        required_scope="staff.holds.write",
        risk_class=ActionRiskClass.HIGH,
        executor=execute_staff_unhold,
        verifier=verify_staff_unhold,
    )
)

ACTION_REGISTRY.register(
    ActionDefinition(
        name="code_request.create",
        description="Create a tracked Code Request work item.",
        params_schema={"title": "string", "repo": "string", "description": "string?", "priority": "string?"},
        required_scope="code_requests.write",
        risk_class=ActionRiskClass.MEDIUM,
        executor=execute_code_request_create,
    )
)

ACTION_REGISTRY.register(
    ActionDefinition(
        name="board.propose",
        description="Submit a proposal to the Board of Directors.",
        params_schema={"title": "string", "proposal": "string", "target": "string?"},
        required_scope="board.proposals.write",
        risk_class=ActionRiskClass.MEDIUM,
        executor=execute_board_propose,
    )
)

ACTION_REGISTRY.register(
    ActionDefinition(
        name="maintenance.runner_restart",
        description="Restart a runner service.",
        params_schema={"runner_name": "string"},
        required_scope="runners.control",
        risk_class=ActionRiskClass.MEDIUM,
        executor=lambda p, c: execute_maintenance_action(p, c, "maintenance.runner_restart"),
    )
)

ACTION_REGISTRY.register(
    ActionDefinition(
        name="maintenance.runner_stop",
        description="Stop a runner service.",
        params_schema={"runner_name": "string"},
        required_scope="runners.control",
        risk_class=ActionRiskClass.HIGH,
        executor=lambda p, c: execute_maintenance_action(p, c, "maintenance.runner_stop"),
    )
)

ACTION_REGISTRY.register(
    ActionDefinition(
        name="maintenance.diagnose",
        description="Run non-invasive diagnostics on a runner or node.",
        params_schema={"target": "string?"},
        required_scope="staff.read",
        risk_class=ActionRiskClass.READ,
        executor=lambda p, c: execute_maintenance_action(p, c, "maintenance.diagnose"),
    )
)
