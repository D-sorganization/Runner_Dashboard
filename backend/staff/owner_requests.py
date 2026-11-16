"""Execute owner-confirmed chat proposals under the requester's principal (#1786, #4274 E-18).

A chat turn may auto-run a model-proposed action only when the sender explicitly listed
that action in ``message.meta.confirm_actions`` *and* :func:`request_approves` holds.
Sending a message is not approval: without per-action confirmation, proposals stay
``proposed`` until ``POST /api/v1/staff/proposals/{id}/decide`` (or execute).

Every normal gate still applies inside :func:`staff.actions.execute_proposal`: the
proposing role's permission and :func:`staff.actions.check_approval_policy` for the
requester.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

import anyio
from identity import Principal, principal_has_scope
from staff.actions import ACTION_REGISTRY, ActionRiskClass, execute_proposal, stored_params_hash
from staff.conversations import ConversationStore
from staff.proposal_cards import MessagePublisher, refresh_proposal_card

log = logging.getLogger("dashboard.staff.owner_requests")

CONFIRM_ACTIONS_META_KEY = "confirm_actions"

#: Risks eligible after explicit per-action confirmation; CRITICAL and owner-only still need a tap.
REQUEST_APPROVES_RISKS = frozenset({ActionRiskClass.READ, ActionRiskClass.LOW, ActionRiskClass.MEDIUM})
#: Barb lanes are full peers (owner decision 2026-10-31, #3647): when the owner asks a Barb
#: in chat, HIGH actions such as ``staff.hold``/``staff.unhold`` run on that request too.
#: CRITICAL and owner-only actions still need an explicit tap.
BARB_REQUEST_APPROVES_RISKS = REQUEST_APPROVES_RISKS | {ActionRiskClass.HIGH}


def message_confirms_action(message_meta: dict[str, Any] | None, action_name: str) -> bool:
    """True when the sender listed *action_name* in ``meta.confirm_actions`` (#4274 E-18)."""
    if not message_meta:
        return False
    raw = message_meta.get(CONFIRM_ACTIONS_META_KEY)
    if not isinstance(raw, list):
        return False
    return action_name in raw


def is_barb_role(role_name: str | None) -> bool:
    """True for the Barb front door (``barb``) and every Barb lane (``rd-barb``, ``*-barb``)."""
    name = (role_name or "").strip().lower()
    return name == "barb" or name.endswith("-barb")


def request_approves(
    requester: Principal | None,
    action_name: str,
    role_name: str | None = None,
    *,
    message_meta: dict[str, Any] | None = None,
) -> bool:
    """True when the sender confirmed *action_name* and may approve that risk.

    *role_name* is the answering role; Barb roles also take HIGH actions when confirmed.
    """
    if not message_confirms_action(message_meta, action_name):
        return False
    action = ACTION_REGISTRY.get(action_name)
    risks = BARB_REQUEST_APPROVES_RISKS if is_barb_role(role_name) else REQUEST_APPROVES_RISKS
    if requester is None or action is None or action.risk_class not in risks:
        return False
    return principal_has_scope(requester, "staff.approve")


async def run_for_requester(
    store: ConversationStore,
    bus: MessagePublisher,
    proposal_id: str,
    requester: Principal,
) -> bool:
    """Execute *proposal_id* as approved by *requester*; return whether it ran.

    Pre: the proposal exists and is ``proposed``. Post: on ``True`` the proposal is in a
    terminal execution state; on ``False`` a policy check refused it, exactly as it would
    refuse a tap, and the refusal is logged. Either way the card shows the current state.
    """
    # No preview in this path: the hash is computed server-side from the stored proposal (#1807).
    run = partial(
        execute_proposal,
        proposal_id,
        approver=requester,
        store=store,
        approve=True,
        params_hash=stored_params_hash(store, proposal_id),
    )
    try:
        # Executors such as board.convene block on the event loop (run_on_loop), so run off it.
        await anyio.to_thread.run_sync(run)
    except Exception as exc:  # noqa: BLE001 - a refusal leaves the card for the owner
        log.info("Proposal %s left for approval: %s", proposal_id, exc)
        return False
    finally:
        await refresh_proposal_card(store, bus, proposal_id)
    return True
