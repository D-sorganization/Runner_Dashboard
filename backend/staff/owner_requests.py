"""Run what the owner asked for without a second approval (Runner_Dashboard#1786).

Owner direction: "if I ask barb, she should be able to do it." When the person who
sent a chat message may approve (``staff.approve``), the actions the answering role
proposes in that turn are executed at once under that person's principal. The card
is still posted and then refreshed, so the thread shows exactly what ran.

Every normal gate still applies inside :func:`staff.actions.execute_proposal`: the
proposing role's permission and :func:`staff.actions.check_approval_policy` for the
requester. Risks above MEDIUM keep their card for an explicit tap.
"""

from __future__ import annotations

import logging
from functools import partial

import anyio
from identity import Principal, principal_has_scope
from staff.actions import ACTION_REGISTRY, ActionRiskClass, execute_proposal, stored_params_hash
from staff.conversations import ConversationStore
from staff.proposal_cards import MessagePublisher, refresh_proposal_card

log = logging.getLogger("dashboard.staff.owner_requests")

#: Risks the requester's own message approves; anything higher waits for a tap.
REQUEST_APPROVES_RISKS = frozenset({ActionRiskClass.READ, ActionRiskClass.LOW, ActionRiskClass.MEDIUM})
#: Barb lanes are full peers (owner decision 2026-10-31, #3647): when the owner asks a Barb
#: in chat, HIGH actions such as ``staff.hold``/``staff.unhold`` run on that request too.
#: CRITICAL and owner-only actions still need an explicit tap.
BARB_REQUEST_APPROVES_RISKS = REQUEST_APPROVES_RISKS | {ActionRiskClass.HIGH}


def is_barb_role(role_name: str | None) -> bool:
    """True for the Barb front door (``barb``) and every Barb lane (``rd-barb``, ``*-barb``)."""
    name = (role_name or "").strip().lower()
    return name == "barb" or name.endswith("-barb")


def request_approves(requester: Principal | None, action_name: str, role_name: str | None = None) -> bool:
    """True when *requester*'s message is itself the approval for *action_name*.

    *role_name* is the answering role; Barb roles also take HIGH actions on request.
    """
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
