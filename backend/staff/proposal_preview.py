"""Read-only, per-viewer preview of a proposal's approval authority (#1807).

The preview tells the calling principal whether approving would be accepted and gives the hash of
the parameters they are looking at. It is computed per request and never stored on the shared
ActionCard (every viewer would see the same verdict). It confers nothing: execution re-evaluates
authority with the live principal and re-verifies the hash.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from identity import Principal
from staff.actions import ACTION_REGISTRY, check_role_permission, compute_params_hash, proposing_role_of
from staff.approval_authority import ApprovalAuthority, evaluate_approval_authority

if TYPE_CHECKING:
    from staff.conversation_models import ActionProposalRecord
    from staff.conversations import ConversationStore


def evaluate_caller_authority(
    store: ConversationStore, prop: ActionProposalRecord, caller: Principal, *, for_denial: bool = False
) -> ApprovalAuthority:
    """The ONE verdict on whether ``caller`` may decide ``prop``; preview and decide both call it (#1942).

    ``for_denial`` keeps every check on the CALLER's authority but drops the gates that only make
    approving unsafe (expiry, a proposing role not permitted to invoke the action): an authorized
    caller must always be able to reject a stale or improper proposal.
    """
    action = ACTION_REGISTRY.get(prop.action)
    role = proposing_role_of(store, prop)
    return evaluate_approval_authority(
        action,
        prop,
        caller,
        role_permitted=for_denial or not role or check_role_permission(action, role),
        proposing_role=role,
        ignore_expiry=for_denial,
    )


def build_proposal_preview(store: ConversationStore, prop: ActionProposalRecord, caller: Principal) -> dict[str, Any]:
    """``{can_approve, denial_reason, params_hash}`` for ``caller`` on ``prop``.

    Post: ``can_approve`` is exactly the shared evaluator's verdict; ``denial_reason`` is ``None``
    iff ``can_approve``. Raises :class:`ParamsHashUnavailableError` (no partial preview) when the
    params cannot be hashed. No state is read for write and nothing is changed.
    """
    verdict = evaluate_caller_authority(store, prop, caller)
    return {
        "can_approve": verdict.allowed,
        "denial_reason": None if verdict.allowed else verdict.reason,
        "params_hash": compute_params_hash(prop),
    }
