"""Single source of truth for who may approve a proposal, and the reviewed-parameter hash (#1807).

``evaluate_approval_authority`` is called by execution (``staff.actions.check_approval_policy``)
and by the read-only preview (``staff.proposal_preview``), so the ``can_approve`` a viewer sees
is the verdict execution will reach for the same principal. The preview confers nothing:
execution re-evaluates with the live principal and never reads a preview.

``params_hash`` is the canonical digest of what a reviewer approves (action plus params). It is
computed for the preview and re-verified at execution, before any state change.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from identity import Principal, principal_has_scope
from staff.conversation_models import PROPOSAL_TTL_SECONDS, ActionProposalRecord

if TYPE_CHECKING:
    from staff.actions import ActionDefinition

UTC = getattr(_dt, "UTC", _dt.UTC)
datetime = _dt.datetime


class ActionRiskClass:
    READ = "read"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    OWNER_ONLY = "owner-only"


OWNER_APPROVED_RISKS = frozenset({ActionRiskClass.HIGH, ActionRiskClass.CRITICAL, ActionRiskClass.OWNER_ONLY})
# States from which an approve (decide, retry of a failed run, or execute of an approved one) is still possible.
DECIDABLE_STATES = frozenset({"proposed", "approved", "failed"})

CODE_OK = "ok"
CODE_EXPIRED = "expired"
CODE_TERMINAL = "terminal"
CODE_OWNER_REQUIRED = "owner_required"
CODE_SCOPE_MISSING = "scope_missing"
CODE_ACTION_SCOPE_MISSING = "action_scope_missing"
CODE_ROLE_NOT_PERMITTED = "role_not_permitted"


@dataclass(frozen=True)
class ApprovalAuthority:
    """Verdict for one principal on one proposal. Invariant: ``reason`` is non-empty iff not ``allowed``."""

    allowed: bool
    reason: str = ""
    code: str = CODE_OK

    def __post_init__(self) -> None:
        if self.allowed == bool(self.reason):
            raise ValueError("ApprovalAuthority.reason must be non-empty exactly when the verdict is a denial")


def _deny(code: str, reason: str) -> ApprovalAuthority:
    return ApprovalAuthority(allowed=False, reason=reason, code=code)


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
    """Return True if principal has owner/admin privileges.

    Matches loosely on id/name ``owner``/``operator``; mirrored as-is (tightening is a follow-up).
    """
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


def evaluate_approval_authority(
    action: ActionDefinition | None,
    prop: ActionProposalRecord,
    approver: Principal,
    *,
    include_state: bool = True,
    role_permitted: bool = True,
    proposing_role: str = "",
) -> ApprovalAuthority:
    """Whether ``approver`` may approve ``prop`` right now, and if not, why (fixed, param-free reasons).

    ``include_state`` adds the "still decidable" gate; ``execute_proposal`` enforces state itself with
    typed errors, so ``check_approval_policy`` passes ``False``. ``role_permitted`` carries the
    proposing-role verdict (``staff.actions.check_role_permission``), computed by the caller.
    """
    if is_proposal_expired(prop):
        return _deny(CODE_EXPIRED, f"Proposal {prop.id} has expired (exceeded 24h TTL)")
    if include_state and prop.state not in DECIDABLE_STATES:
        return _deny(CODE_TERMINAL, f"Proposal {prop.id} is '{prop.state}' and can no longer be approved")
    if not role_permitted:
        return _deny(CODE_ROLE_NOT_PERMITTED, f"Role '{proposing_role}' is not permitted to invoke '{prop.action}'")

    risk = action.risk_class if action else prop.risk
    owner = is_owner(approver)
    if risk in OWNER_APPROVED_RISKS and not owner:
        return _deny(CODE_OWNER_REQUIRED, f"Action '{prop.action}' has risk '{risk}' and requires owner approval")
    # Issue #1789: decide-capable roles approve via their preset scopes, exactly like
    # the routes' require_scope does; principals without an explicit scope or a preset
    # granting "staff.approve" stay fail-closed.
    if not owner and not principal_has_scope(approver, "staff.approve"):
        return _deny(CODE_SCOPE_MISSING, f"Principal '{approver.id}' lacks 'staff.approve' scope")
    if action and not owner and not principal_has_scope(approver, action.required_scope):
        return _deny(
            CODE_ACTION_SCOPE_MISSING,
            f"Principal '{approver.id}' lacks '{action.required_scope}' required by '{action.name}'",
        )
    return ApprovalAuthority(allowed=True)


def params_hash(action: str, params: Mapping[str, Any]) -> str:
    """SHA-256 hex of the canonical JSON of ``{"action", "params"}``: what a reviewer approves.

    Canonical: sorted keys, compact separators, UTF-8, no NaN. Risk and state are excluded
    (registry-derived, not reviewer input).
    """
    canonical = json.dumps(
        {"action": action, "params": dict(params)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
