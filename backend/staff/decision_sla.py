"""Decision SLA: apply a proposal's default once the owner is silent past ``decide_by`` (WP-2.6, #1607).

A source may give a proposal a ``decide_by`` deadline and a ``default_if_silent`` of ``approve``
or ``deny``. Barb's follow-up sweep calls :func:`apply_decision_defaults`:

- ``deny`` is written back to the proposal store as a decision by ``barb``;
- ``approve`` runs through :func:`staff.actions.execute_proposal`, so every policy check, the
  verifier and the result card apply exactly as for a human approval;
- both are recorded with ``record_audit`` (``decision_default_applied``).

``approve`` by silence is limited to :data:`SILENT_APPROVE_RISKS`. The risk is re-read from the
action registry at sweep time; an action that became riskier since creation is refused
(``decision_default_refused``) and stays with the owner. A proposal without a default is left to
the caller, which only pings.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from identity import Principal
from staff.actions import ACTION_REGISTRY, execute_proposal
from staff.audit import record_audit
from staff.conversation_models import SILENT_APPROVE_RISKS, ActionProposalRecord
from staff.conversations import ConversationStore

log = logging.getLogger("dashboard.staff.decision_sla")

BARB = "barb"
Outcome = Literal["denied", "executed", "failed", "refused"]


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    proposal_id: str
    applied: str
    outcome: Outcome
    detail: str = ""


def _deadline(prop: ActionProposalRecord) -> datetime | None:
    if not prop.decide_by:
        return None
    parsed = datetime.fromisoformat(prop.decide_by.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def overdue_decisions(store: ConversationStore, now: datetime) -> list[ActionProposalRecord]:
    """Undecided proposals whose ``decide_by`` has passed, oldest first."""
    due = []
    for prop in store.list_proposals(state="proposed", limit=500):
        deadline = _deadline(prop)
        if deadline is not None and deadline <= now:
            due.append(prop)
    return sorted(due, key=lambda p: p.decide_by or "")


def _barb_approver(required_scope: str) -> Principal:
    """Barb approves only what silence may approve: ``staff.approve`` plus the action's own scope."""
    return Principal(id=BARB, type="bot", name="Barb", roles=[BARB], scopes=["staff.approve", required_scope])


def _approve(store: ConversationStore, prop: ActionProposalRecord) -> DecisionOutcome:
    action = ACTION_REGISTRY.get(prop.action)
    risk = action.risk_class if action else prop.risk
    if action is None or risk not in SILENT_APPROVE_RISKS:
        return DecisionOutcome(prop.id, "approve", "refused", f"action {prop.action!r} is risk {risk!r}")
    try:
        result = execute_proposal(prop.id, approver=_barb_approver(action.required_scope), store=store, approve=True)
    except Exception as exc:  # noqa: BLE001 - a policy refusal leaves the proposal with the owner
        return DecisionOutcome(prop.id, "approve", "refused", str(exc))
    if result.success:
        return DecisionOutcome(prop.id, "approve", "executed")
    return DecisionOutcome(prop.id, "approve", "failed", result.error or "")


def _deny(store: ConversationStore, prop: ActionProposalRecord) -> DecisionOutcome:
    reason = f"decision SLA: no owner decision by {prop.decide_by}; default applied"
    store.decide_proposal(prop.id, "denied", decided_by=BARB, reason=reason)
    return DecisionOutcome(prop.id, "deny", "denied")


def apply_decision_defaults(
    store: ConversationStore, now: datetime | None = None, skip: Collection[str] = ()
) -> list[DecisionOutcome]:
    """Apply ``default_if_silent`` to every overdue proposal that has one, except ids in ``skip``.

    Post: each returned outcome is audited; a ``refused`` proposal is still ``proposed``.
    """
    current = now or datetime.now(UTC)
    outcomes: list[DecisionOutcome] = []
    for prop in overdue_decisions(store, current):
        if not prop.default_if_silent or prop.id in skip:
            continue
        outcome = _approve(store, prop) if prop.default_if_silent == "approve" else _deny(store, prop)
        record_audit(
            action="decision_default_refused" if outcome.outcome == "refused" else "decision_default_applied",
            target=prop.id,
            principal=BARB,
            surface="barb",
            thread_id=prop.thread_id,
            outcome="success" if outcome.outcome in ("denied", "executed") else "failure",
            detail={
                "decide_by": prop.decide_by,
                "default_if_silent": prop.default_if_silent,
                "outcome": outcome.outcome,
                "detail": outcome.detail,
            },
            store=getattr(store, "_audit_store", None),
        )
        outcomes.append(outcome)
    return outcomes
